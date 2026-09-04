"""
Tests for storage metrics, retention prune worker, healthcheck, settings encryption, and host aliases.
"""

import asyncio
import datetime
import os
import stat
from pathlib import Path
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core import pipeline as pipeline_mod
from app.core.migrations import get_connection, run_migrations
from app.core.rate_limiter import login_rate_limiter
from app.core.security import (
    SESSION_COOKIE_NAME,
    create_session_token,
    decrypt_value,
    get_or_create_master_key,
    reset_crypto_cache,
)
from app.core.sse import sse_manager
from app.main import _supervise_worker, create_app
from app.services.retention import PruneWorker
from app.services.storage_metrics import (
    StorageMetricsWorker,
    prune_old_metrics,
    record_metrics,
    sample_storage_metrics,
)


@pytest.fixture(autouse=True)
def reset_system_env(tmp_path: Path, monkeypatch):
    pipeline_mod._log_queue = None
    pipeline_mod._dropped_logs_total = 0
    login_rate_limiter.reset()
    reset_crypto_cache()
    sse_manager.reset()

    db_file = tmp_path / "logs.db"
    key_file = tmp_path / ".secret_key"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("SECRET_KEY_PATH", str(key_file))
    monkeypatch.delenv("LOGSHED_SECRET_KEY", raising=False)

    run_migrations(db_file)
    get_or_create_master_key(key_file)

    yield

    pipeline_mod._log_queue = None
    pipeline_mod._dropped_logs_total = 0
    login_rate_limiter.reset()
    reset_crypto_cache()
    sse_manager.reset()


@pytest_asyncio.fixture
async def client():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def auth_cookie() -> dict[str, str]:
    token = create_session_token(user_id=1)
    return {SESSION_COOKIE_NAME: token}


def _seed_logs(db_path: Path, entries: list[dict]):
    query = """
        INSERT INTO logs (
            timestamp, received_at, source_ip, source_alias,
            app_name, facility, severity, message, raw
        ) VALUES (
            :timestamp, :received_at, :source_ip, :source_alias,
            :app_name, :facility, :severity, :message, :raw
        )
    """
    with get_connection(db_path) as conn:
        conn.executemany(query, entries)
        conn.commit()


# ===================================================================
# 1. Storage Metrics Sampling & History
# ===================================================================

class TestStorageMetrics:

    def test_sample_returns_correct_keys(self, tmp_path: Path):
        db_file = tmp_path / "logs.db"
        metrics = sample_storage_metrics(db_file)
        assert "recorded_at" in metrics
        assert "db_size_bytes" in metrics
        assert "disk_free_bytes" in metrics
        assert "disk_total_bytes" in metrics
        assert "total_logs_count" in metrics

    def test_db_size_bytes_positive(self, tmp_path: Path):
        db_file = tmp_path / "logs.db"
        metrics = sample_storage_metrics(db_file)
        assert metrics["db_size_bytes"] > 0

    def test_disk_values_sane(self, tmp_path: Path):
        db_file = tmp_path / "logs.db"
        metrics = sample_storage_metrics(db_file)
        assert metrics["disk_total_bytes"] > 0
        assert metrics["disk_free_bytes"] >= 0
        assert metrics["disk_total_bytes"] >= metrics["disk_free_bytes"]

    def test_total_logs_count_zero_initially(self, tmp_path: Path):
        db_file = tmp_path / "logs.db"
        metrics = sample_storage_metrics(db_file)
        assert metrics["total_logs_count"] == 0

    def test_total_logs_count_after_inserts(self, tmp_path: Path):
        db_file = tmp_path / "logs.db"
        conn = get_connection(db_file)
        for i in range(5):
            conn.execute(
                """INSERT INTO logs (timestamp, received_at, source_ip, source_alias,
                   app_name, facility, severity, message, raw)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("2024-01-15T10:00:00", "2024-01-15T10:00:00",
                 "10.0.0.1", "myhost", "app", 1, 6, f"msg{i}", "raw"),
            )
        conn.commit()
        conn.close()

        metrics = sample_storage_metrics(db_file)
        assert metrics["total_logs_count"] == 5

    def test_record_metrics_writes_row(self, tmp_path: Path):
        db_file = tmp_path / "logs.db"
        metrics = record_metrics(db_file)

        conn = get_connection(db_file)
        rows = conn.execute("SELECT * FROM storage_metrics").fetchall()
        conn.close()

        assert len(rows) == 1
        row = rows[0]
        assert row[1] == metrics["recorded_at"]
        assert row[2] == metrics["db_size_bytes"]
        assert row[3] == metrics["disk_free_bytes"]
        assert row[4] == metrics["disk_total_bytes"]
        assert row[5] == metrics["total_logs_count"]

    def test_prune_old_metrics(self, tmp_path: Path):
        db_file = tmp_path / "logs.db"
        conn = get_connection(db_file)
        old_time = (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=31)
        ).isoformat()
        conn.execute(
            """INSERT INTO storage_metrics 
            (recorded_at, db_size_bytes, disk_free_bytes, disk_total_bytes, total_logs_count)
            VALUES (?, ?, ?, ?, ?)""",
            (old_time, 1000, 2000, 3000, 0),
        )
        recent_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO storage_metrics 
            (recorded_at, db_size_bytes, disk_free_bytes, disk_total_bytes, total_logs_count)
            VALUES (?, ?, ?, ?, ?)""",
            (recent_time, 1000, 2000, 3000, 0),
        )
        conn.commit()
        conn.close()

        deleted = prune_old_metrics(db_file)
        assert deleted == 1

        conn = get_connection(db_file)
        remaining = conn.execute("SELECT COUNT(*) FROM storage_metrics").fetchone()[0]
        conn.close()
        assert remaining == 1


# ===================================================================
# 2. Retention Prune Worker & Maintenance
# ===================================================================

class TestRetentionAndPruneWorker:

    @pytest.mark.asyncio
    async def test_maintenance_prune_and_storage_metrics(
        self, client: AsyncClient, auth_cookie: dict, tmp_path: Path
    ):
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        db_file = tmp_path / "logs.db"

        old_time = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=45)).isoformat()
        fresh_time = datetime.datetime.now(datetime.timezone.utc).isoformat()

        entries = [
            {
                "timestamp": old_time,
                "received_at": old_time,
                "source_ip": "10.0.0.1",
                "source_alias": "srv1",
                "app_name": "app",
                "facility": 1,
                "severity": 6,
                "message": "old log to prune",
                "raw": "raw old",
            },
            {
                "timestamp": fresh_time,
                "received_at": fresh_time,
                "source_ip": "10.0.0.1",
                "source_alias": "srv1",
                "app_name": "app",
                "facility": 1,
                "severity": 6,
                "message": "fresh log to keep",
                "raw": "raw fresh",
            },
        ]
        _seed_logs(db_file, entries)

        res_prune = await client.post("/api/maintenance/prune")
        assert res_prune.status_code == 200
        prune_data = res_prune.json()
        assert prune_data["status"] == "ok"
        assert prune_data["deleted_logs"] == 1
        assert "metrics" in prune_data
        assert prune_data["metrics"]["total_logs_count"] == 1

        res_storage = await client.get("/api/system/storage")
        assert res_storage.status_code == 200
        storage_data = res_storage.json()
        assert storage_data["total_logs_count"] == 1
        assert len(storage_data["history"]) >= 1

    @pytest.mark.asyncio
    async def test_prune_worker_daily_task(self, tmp_path: Path):
        db_file = tmp_path / "logs.db"
        old_time = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=45)).isoformat()
        entries = [
            {
                "timestamp": old_time,
                "received_at": old_time,
                "source_ip": "10.0.0.1",
                "source_alias": "srv1",
                "app_name": "app",
                "facility": 1,
                "severity": 6,
                "message": "old log pruned by worker",
                "raw": "raw old",
            }
        ]
        _seed_logs(db_file, entries)

        worker = PruneWorker(db_file)
        task = asyncio.create_task(worker.run())

        await asyncio.sleep(0.1)
        await worker.stop()
        await task

        with get_connection(db_file) as conn:
            count = conn.execute("SELECT COUNT(*) FROM logs WHERE message = 'old log pruned by worker'").fetchone()[0]
            assert count == 0

    @pytest.mark.asyncio
    async def test_storage_metrics_worker_exception_backoff(self, tmp_path: Path):
        invalid_path = tmp_path / "non_existent" / "db.sqlite"
        worker = StorageMetricsWorker(invalid_path)

        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.05)
        await worker.stop()
        await task
        assert not worker._running

    @pytest.mark.asyncio
    async def test_prune_worker_exception_backoff(self, tmp_path: Path):
        invalid_path = tmp_path / "non_existent" / "db.sqlite"
        worker = PruneWorker(invalid_path)

        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.05)
        await worker.stop()
        await task
        assert not worker._running

    @pytest.mark.asyncio
    async def test_supervise_worker_restarts_on_error(self):
        attempts = 0

        async def failing_worker():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("Simulated transient worker crash")
            await asyncio.sleep(0.5)

        task = asyncio.create_task(_supervise_worker(failing_worker, "TestWorker"))
        await asyncio.sleep(1.2)
        assert attempts >= 2
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


# ===================================================================
# 3. System Healthcheck
# ===================================================================

class TestSystemHealthcheck:

    @pytest.mark.asyncio
    async def test_health_check(self, client: AsyncClient):
        res = await client.get("/api/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["db"] == "ok"
        assert isinstance(data["queue_depth"], int)
        assert isinstance(data["dropped_logs"], int)
        assert "ingest_rate" in data
        assert isinstance(data["ingest_rate"], (int, float))
        assert data["ingest_rate"] >= 0.0


# ===================================================================
# 4. Settings Encryption & Key Management
# ===================================================================

class TestSettingsEncryptionAndKeyManagement:

    def test_secret_key_generated_with_0600_permissions(self, tmp_path: Path, monkeypatch):
        reset_crypto_cache()
        key_file = tmp_path / "perms_test" / ".secret_key"
        monkeypatch.setenv("SECRET_KEY_PATH", str(key_file))

        key = get_or_create_master_key(key_file)
        assert len(key) > 0
        assert key_file.exists()

        mode = stat.S_IMODE(os.stat(key_file).st_mode)
        assert mode == 0o600

    def test_secret_key_environment_variable_override(self, monkeypatch):
        reset_crypto_cache()
        override_key = "custom_super_secret_override_key_12345"
        monkeypatch.setenv("LOGSHED_SECRET_KEY", override_key)

        key = get_or_create_master_key()
        assert key is not None
        reset_crypto_cache()
        key2 = get_or_create_master_key()
        assert key == key2

    @pytest.mark.asyncio
    async def test_settings_encrypted_at_rest_and_never_exposed(
        self, client: AsyncClient, auth_cookie: dict, tmp_path: Path
    ):
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])

        payload = {
            "ai_provider": "openai",
            "ai_model": "gpt-4o",
            "ai_api_key": "sk-1234567890abcdef1234567890",
            "ai_base_url": "https://api.openai.com/v1",
            "retention_days": 14,
        }
        res_post = await client.post("/api/settings", json=payload)
        assert res_post.status_code == 200

        db_file = tmp_path / "logs.db"
        with get_connection(db_file) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value, is_encrypted FROM system_settings")
            rows = {r[0]: (r[1], bool(r[2])) for r in cursor.fetchall()}

        assert rows["ai_api_key"][1] is True
        assert rows["ai_api_key"][0] != "sk-1234567890abcdef1234567890"
        assert decrypt_value(rows["ai_api_key"][0]) == "sk-1234567890abcdef1234567890"

        assert rows["retention_days"][1] is False
        assert rows["retention_days"][0] == "14"

        res_get = await client.get("/api/settings")
        assert res_get.status_code == 200
        data = res_get.json()

        assert data["ai_api_key"] == "********"
        assert data["has_ai_api_key"] is True
        assert data["retention_days"] == 14
        assert data["ai_provider"] == "openai"

        update2 = {
            "ai_provider": "gemini",
            "ai_api_key": "********",
            "retention_days": 28,
        }
        res_post2 = await client.post("/api/settings", json=update2)
        assert res_post2.status_code == 200

        with get_connection(db_file) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM system_settings WHERE key = 'ai_api_key'")
            val = cursor.fetchone()[0]
            assert decrypt_value(val) == "sk-1234567890abcdef1234567890"

        res_valid = await client.post("/api/settings", json={"retention_days": 365})
        assert res_valid.status_code == 200

        res_invalid = await client.post("/api/settings", json={"retention_days": 366})
        assert res_invalid.status_code == 422

        res_invalid_low = await client.post("/api/settings", json={"retention_days": 0})
        assert res_invalid_low.status_code == 422

        res_invalid2 = await client.post("/api/settings", json={"retention_days": 3650})
        assert res_invalid2.status_code == 422


# ===================================================================
# 5. Host Aliases CRUD & Retroactive Updates
# ===================================================================

class TestHostAliases:

    @pytest.mark.asyncio
    async def test_host_alias_crud_workflow(self, client: AsyncClient, auth_cookie: dict):
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])

        create_res = await client.post(
            "/api/aliases",
            json={"ip": "192.168.1.10", "alias": "router-primary", "notes": "Main OPNsense router"},
        )
        assert create_res.status_code == 200
        assert create_res.json()["alias"] == "router-primary"

        list_res = await client.get("/api/aliases")
        assert list_res.status_code == 200
        aliases = list_res.json()
        assert len(aliases) == 1
        assert aliases[0]["ip"] == "192.168.1.10"

        update_res = await client.post(
            "/api/aliases",
            json={"ip": "192.168.1.10", "alias": "router-gateway", "notes": "Updated note"},
        )
        assert update_res.status_code == 200
        assert update_res.json()["alias"] == "router-gateway"

        del_res = await client.delete("/api/aliases/192.168.1.10")
        assert del_res.status_code == 200
        assert del_res.json()["status"] == "ok"

        del_404 = await client.delete("/api/aliases/192.168.1.10")
        assert del_404.status_code == 404

    @pytest.mark.asyncio
    async def test_host_alias_retroactively_updates_existing_logs(
        self, client: AsyncClient, auth_cookie: dict, tmp_path: Path
    ):
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        db_file = tmp_path / "logs.db"

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with get_connection(db_file) as conn:
            conn.execute(
                """INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
                   VALUES (?, ?, '192.168.1.99', '192.168.1.99', 'kernel', 1, 3, 'Link down on eth0', 'raw log 1')""",
                (now, now),
            )
            conn.execute(
                """INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
                   VALUES (?, ?, '192.168.1.99', '192.168.1.99', 'dhcp', 1, 6, 'Assigned 192.168.1.105', 'raw log 2')""",
                (now, now),
            )
            conn.commit()

        res_before = await client.get("/api/logs", params={"source": "192.168.1.99"})
        assert res_before.status_code == 200
        assert res_before.json()["total"] == 2

        create_res = await client.post(
            "/api/aliases",
            json={"ip": "192.168.1.99", "alias": "switch-core", "notes": "Core Managed Switch"},
        )
        assert create_res.status_code == 200

        res_alias = await client.get("/api/logs", params={"source": "switch-core"})
        assert res_alias.status_code == 200
        assert res_alias.json()["total"] == 2
        for log in res_alias.json()["logs"]:
            assert log["source_alias"] == "switch-core"
            assert log["source_ip"] == "192.168.1.99"

        update_res = await client.post(
            "/api/aliases",
            json={"ip": "192.168.1.99", "alias": "switch-aggregation", "notes": "Renamed switch"},
        )
        assert update_res.status_code == 200

        res_updated = await client.get("/api/logs", params={"source": "switch-aggregation"})
        assert res_updated.status_code == 200
        assert res_updated.json()["total"] == 2
        for log in res_updated.json()["logs"]:
            assert log["source_alias"] == "switch-aggregation"

        del_res = await client.delete("/api/aliases/192.168.1.99")
        assert del_res.status_code == 200

        res_reverted = await client.get("/api/logs", params={"source": "192.168.1.99"})
        assert res_reverted.status_code == 200
        assert res_reverted.json()["total"] == 2
        for log in res_reverted.json()["logs"]:
            assert log["source_alias"] == "192.168.1.99"
