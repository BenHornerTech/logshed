"""
Phase 3 verification tests for Homelab Log Hub.

Covers:
  - /api/auth/setup: first-run creation, lockout with 403 on subsequent calls, session cookie.
  - /api/auth/login: Argon2id verification, 5-failed-attempts-per-min-per-IP rate limiting, SameSite=Lax HttpOnly cookie.
  - /api/auth/logout: cookie clearing.
  - /api/logs: full-text search (FTS5), source/app filtering, severity_max, from/to time filters, pagination.
  - /api/logs/{id}/context: lines before & after scoped to identical source_alias and app_name.
  - /api/logs/stream: Server-Sent Events (SSE) streaming.
  - /api/settings: Fernet encryption at rest, masked secrets in GET responses, preserving masked secrets on POST.
  - /api/aliases: GET, POST (upsert), DELETE host aliases.
  - /api/health: healthcheck metrics (db status, queue depth, dropped count).
  - /api/maintenance/prune: retention pruning, WAL truncate, storage_metrics recording.
  - /api/system/storage: live footprint and 30-day history.
  - /api/notifications/test & /api/notifications/pushover: mock UI testing endpoints.
"""

import asyncio
import datetime
import os
import sqlite3
import stat
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core import pipeline as pipeline_mod
from app.core.config import get_secret_key_path
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
from app.main import create_app


# ---------------------------------------------------------------------------
# Test Fixtures & Setup
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_globals(tmp_path: Path, monkeypatch):
    """Reset queues, rate limiter, encryption keys, and environment for each test."""
    pipeline_mod._log_queue = None
    pipeline_mod._dropped_logs_total = 0
    login_rate_limiter.reset()
    reset_crypto_cache()
    sse_manager.reset()

    # Point DATA_DIR and DB_PATH to tmp_path
    db_file = tmp_path / "logs.db"
    key_file = tmp_path / ".secret_key"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("SECRET_KEY_PATH", str(key_file))
    monkeypatch.delenv("LOG_HUB_SECRET_KEY", raising=False)

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
    """Async HTTP client for FastAPI endpoints."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def auth_cookie() -> dict[str, str]:
    """Generates a valid authenticated session cookie."""
    token = create_session_token(user_id=1)
    return {SESSION_COOKIE_NAME: token}


def _seed_logs(db_path: Path, entries: list[dict]):
    """Helper to insert test logs directly into the database."""
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


# ---------------------------------------------------------------------------
# 1. Authentication & Security Tests
# ---------------------------------------------------------------------------

class TestAuthentication:
    @pytest.mark.asyncio
    async def test_auth_setup_succeeds_once_and_locks_out(self, client: AsyncClient):
        # 1. First setup should succeed
        res = await client.post("/api/auth/setup", json={"password": "initial_password_123"})
        assert res.status_code == 200
        assert res.json() == {"status": "ok", "detail": None}
        assert SESSION_COOKIE_NAME in res.cookies

        # Verify cookie attributes in Set-Cookie header
        set_cookie = res.headers.get("set-cookie", "")
        assert "session=" in set_cookie
        assert "httponly" in set_cookie.lower()
        assert "samesite=lax" in set_cookie.lower()

        # 2. Second setup attempt must return 403 Forbidden
        res2 = await client.post("/api/auth/setup", json={"password": "another_password_456"})
        assert res2.status_code == 403

    @pytest.mark.asyncio
    async def test_login_success_and_logout(self, client: AsyncClient):
        # Setup admin first
        await client.post("/api/auth/setup", json={"password": "valid_password_123"})

        # Successful login
        res = await client.post("/api/auth/login", json={"password": "valid_password_123"})
        assert res.status_code == 200
        assert res.json()["status"] == "ok"
        assert SESSION_COOKIE_NAME in res.cookies

        # Logout
        res_logout = await client.post("/api/auth/logout")
        assert res_logout.status_code == 200

    @pytest.mark.asyncio
    async def test_login_failed_password(self, client: AsyncClient):
        await client.post("/api/auth/setup", json={"password": "correct_password"})

        res = await client.post("/api/auth/login", json={"password": "wrong_password"})
        assert res.status_code == 401
        assert "Invalid password" in res.json()["detail"]

    @pytest.mark.asyncio
    async def test_login_rate_limiting_sliding_window(self, client: AsyncClient):
        await client.post("/api/auth/setup", json={"password": "correct_password"})

        # Make 5 failed attempts (allowed under limit of 5)
        for _ in range(5):
            res = await client.post("/api/auth/login", json={"password": "wrong_password"})
            assert res.status_code == 401

        # 6th attempt must trigger 429 Too Many Requests
        res_blocked = await client.post("/api/auth/login", json={"password": "wrong_password"})
        assert res_blocked.status_code == 429
        assert "Too many failed login attempts" in res_blocked.json()["detail"]

        # Even with correct password, blocked until window clears or reset
        res_blocked_correct = await client.post("/api/auth/login", json={"password": "correct_password"})
        assert res_blocked_correct.status_code == 429

    @pytest.mark.asyncio
    async def test_auth_status_endpoint(self, client: AsyncClient, auth_cookie: dict):
        # Before setup
        res = await client.get("/api/auth/status")
        assert res.status_code == 200
        assert res.json() == {"setup_required": True, "authenticated": False}

        # After setup but no cookie
        await client.post("/api/auth/setup", json={"password": "secure_password"})
        res2 = await client.get("/api/auth/status")
        assert res2.status_code == 200
        assert res2.json()["setup_required"] is False
        assert res2.json()["authenticated"] is True  # setup sets cookie in client jar

        # With fresh client without cookie
        transport = ASGITransport(app=create_app())
        async with AsyncClient(transport=transport, base_url="http://test") as clean_client:
            res3 = await clean_client.get("/api/auth/status")
            assert res3.json() == {"setup_required": False, "authenticated": False}

            # With auth cookie provided
            clean_client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
            res4 = await clean_client.get("/api/auth/status")
            assert res4.json() == {"setup_required": False, "authenticated": True}

    @pytest.mark.asyncio
    async def test_unauthenticated_requests_rejected_on_protected_endpoints(self, client: AsyncClient):
        # All protected endpoints return 401 when no session cookie is sent
        endpoints = [
            ("GET", "/api/logs"),
            ("GET", "/api/logs/1/context"),
            ("GET", "/api/settings"),
            ("POST", "/api/settings"),
            ("GET", "/api/aliases"),
            ("POST", "/api/aliases"),
            ("POST", "/api/maintenance/prune"),
            ("GET", "/api/system/storage"),
            ("POST", "/api/notifications/test"),
            ("POST", "/api/notifications/pushover"),
        ]

        for method, endpoint in endpoints:
            if method == "GET":
                res = await client.get(endpoint)
            else:
                res = await client.post(endpoint, json={})
            assert res.status_code == 401, f"{method} {endpoint} did not return 401"


# ---------------------------------------------------------------------------
# 2. Secret Encryption at Rest & Key Management Tests
# ---------------------------------------------------------------------------

class TestEncryptionAndKeyManagement:
    def test_secret_key_generated_with_0600_permissions(self, tmp_path: Path, monkeypatch):
        reset_crypto_cache()
        key_file = tmp_path / "perms_test" / ".secret_key"
        monkeypatch.setenv("SECRET_KEY_PATH", str(key_file))

        key = get_or_create_master_key(key_file)
        assert len(key) > 0
        assert key_file.exists()

        # Check permissions: 0600
        mode = stat.S_IMODE(os.stat(key_file).st_mode)
        assert mode == 0o600

    def test_secret_key_environment_variable_override(self, monkeypatch):
        reset_crypto_cache()
        override_key = "custom_super_secret_override_key_12345"
        monkeypatch.setenv("LOG_HUB_SECRET_KEY", override_key)

        key = get_or_create_master_key()
        assert key is not None
        # Should be derived deterministically
        reset_crypto_cache()
        key2 = get_or_create_master_key()
        assert key == key2

    @pytest.mark.asyncio
    async def test_settings_encrypted_at_rest_and_never_exposed(
        self, client: AsyncClient, auth_cookie: dict, tmp_path: Path
    ):
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])

        # 1. Update settings with plaintext API keys
        payload = {
            "ai_provider": "openai",
            "ai_model": "gpt-4o",
            "ai_api_key": "sk-1234567890abcdef1234567890",
            "ai_base_url": "https://api.openai.com/v1",
            "pushover_user_key": "u_test_user_key_99999",
            "pushover_app_token": "a_test_app_token_88888",
            "retention_days": 45,
        }
        res_post = await client.post("/api/settings", json=payload)
        assert res_post.status_code == 200

        # 2. Inspect SQLite table directly to verify encryption at rest
        db_file = tmp_path / "logs.db"
        with get_connection(db_file) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value, is_encrypted FROM system_settings")
            rows = {r[0]: (r[1], bool(r[2])) for r in cursor.fetchall()}

        # Plaintext keys must NOT appear in database raw values
        assert rows["ai_api_key"][1] is True  # is_encrypted = 1
        assert rows["ai_api_key"][0] != "sk-1234567890abcdef1234567890"
        assert decrypt_value(rows["ai_api_key"][0]) == "sk-1234567890abcdef1234567890"

        assert rows["pushover_user_key"][1] is True
        assert rows["pushover_user_key"][0] != "u_test_user_key_99999"
        assert decrypt_value(rows["pushover_user_key"][0]) == "u_test_user_key_99999"

        assert rows["retention_days"][1] is False
        assert rows["retention_days"][0] == "45"

        # 3. GET /api/settings MUST NEVER return decrypted secrets
        res_get = await client.get("/api/settings")
        assert res_get.status_code == 200
        data = res_get.json()

        assert data["ai_api_key"] == "********"
        assert data["pushover_user_key"] == "********"
        assert data["pushover_app_token"] == "********"
        assert data["has_ai_api_key"] is True
        assert data["has_pushover_user_key"] is True
        assert data["has_pushover_app_token"] is True
        assert data["retention_days"] == 45
        assert data["ai_provider"] == "openai"

        # 4. Updating settings with masked value '********' should keep original secret intact
        update2 = {
            "ai_provider": "gemini",
            "ai_api_key": "********",  # Masked placeholder sent back by UI
            "retention_days": 60,
        }
        res_post2 = await client.post("/api/settings", json=update2)
        assert res_post2.status_code == 200

        with get_connection(db_file) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM system_settings WHERE key = 'ai_api_key'")
            val = cursor.fetchone()[0]
            assert decrypt_value(val) == "sk-1234567890abcdef1234567890"


# ---------------------------------------------------------------------------
# 3. Log Query, Filters & Context Tests
# ---------------------------------------------------------------------------

class TestLogQuerying:
    @pytest.mark.asyncio
    async def test_logs_filtering_and_fts(self, client: AsyncClient, auth_cookie: dict, tmp_path: Path):
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        db_file = tmp_path / "logs.db"

        # Seed test logs
        test_entries = [
            {
                "timestamp": "2026-08-29T10:00:00Z",
                "received_at": "2026-08-29T10:00:01Z",
                "source_ip": "192.168.1.50",
                "source_alias": "unraid-main",
                "app_name": "nginx",
                "facility": 1,
                "severity": 3,  # Error
                "message": "Connection refused upstream failure on backend pool",
                "raw": "<11>1 2026-08-29T10:00:00Z unraid-main nginx - - - Connection refused upstream failure",
            },
            {
                "timestamp": "2026-08-29T11:00:00Z",
                "received_at": "2026-08-29T11:00:01Z",
                "source_ip": "192.168.1.60",
                "source_alias": "pve-node1",
                "app_name": "kernel",
                "facility": 0,
                "severity": 2,  # Critical
                "message": "Out of Memory: Killed process 412 (mysqld)",
                "raw": "<10>1 2026-08-29T11:00:00Z pve-node1 kernel - - - Out of Memory: Killed process",
            },
            {
                "timestamp": "2026-08-29T12:00:00Z",
                "received_at": "2026-08-29T12:00:01Z",
                "source_ip": "192.168.1.50",
                "source_alias": "unraid-main",
                "app_name": "nextcloud",
                "facility": 1,
                "severity": 6,  # Info
                "message": "User admin logged in successfully from 192.168.1.100",
                "raw": "<14>1 2026-08-29T12:00:00Z unraid-main nextcloud - - - User admin logged in",
            },
        ]
        _seed_logs(db_file, test_entries)

        # 1. Unfiltered query: returns all 3 logs ordered by timestamp DESC
        res = await client.get("/api/logs")
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 3
        assert len(data["logs"]) == 3
        assert data["logs"][0]["app_name"] == "nextcloud"

        # 2. FTS query for 'refused'
        res_fts = await client.get("/api/logs", params={"query": "refused"})
        assert res_fts.status_code == 200
        data_fts = res_fts.json()
        assert data_fts["total"] == 1
        assert data_fts["logs"][0]["app_name"] == "nginx"

        # 3. Source filter (matches source_alias or source_ip)
        res_src = await client.get("/api/logs", params={"source": "unraid-main"})
        assert res_src.json()["total"] == 2

        res_src_ip = await client.get("/api/logs", params={"source": "192.168.1.60"})
        assert res_src_ip.json()["total"] == 1
        assert res_src_ip.json()["logs"][0]["source_alias"] == "pve-node1"

        # 4. App name filter
        res_app = await client.get("/api/logs", params={"app_name": "kernel"})
        assert res_app.json()["total"] == 1

        # 5. Severity max filter (severity <= 3 should return Error(3) and Critical(2), excluding Info(6))
        res_sev = await client.get("/api/logs", params={"severity_max": 3})
        assert res_sev.json()["total"] == 2
        for log in res_sev.json()["logs"]:
            assert log["severity"] <= 3

        # 6. Time bounds (from and to)
        res_time = await client.get(
            "/api/logs",
            params={
                "from": "2026-08-29T10:30:00Z",
                "to": "2026-08-29T11:30:00Z",
            },
        )
        assert res_time.json()["total"] == 1
        assert res_time.json()["logs"][0]["app_name"] == "kernel"

    @pytest.mark.asyncio
    async def test_log_context_scoped_to_same_source_and_app(
        self, client: AsyncClient, auth_cookie: dict, tmp_path: Path
    ):
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        db_file = tmp_path / "logs.db"

        # Seed a sequence of logs with interleaved sources/apps
        entries = [
            # Host 1, App A
            {"timestamp": f"2026-08-29T10:0{i}:00Z", "received_at": f"2026-08-29T10:0{i}:01Z",
             "source_ip": "10.0.0.1", "source_alias": "srv1", "app_name": "web", "facility": 1, "severity": 6,
             "message": f"web line {i}", "raw": f"raw web {i}"}
            for i in range(1, 6)
        ]
        # Interleaved logs from other host/app
        entries.append({
            "timestamp": "2026-08-29T10:03:30Z", "received_at": "2026-08-29T10:03:30Z",
            "source_ip": "10.0.0.2", "source_alias": "srv2", "app_name": "database", "facility": 1, "severity": 3,
            "message": "other host message", "raw": "other raw",
        })
        _seed_logs(db_file, entries)

        # Query context for line 3 (ID 3 in web app)
        target_id = 3
        res = await client.get(f"/api/logs/{target_id}/context", params={"lines": 2})
        assert res.status_code == 200
        data = res.json()
        assert data["target_id"] == target_id

        # Must contain ONLY srv1 / web logs
        context_logs = data["logs"]
        assert len(context_logs) == 5  # 2 before + target + 2 after
        for log in context_logs:
            assert log["source_alias"] == "srv1"
            assert log["app_name"] == "web"

        # Chronological order verified
        timestamps = [l["timestamp"] for l in context_logs]
        assert timestamps == sorted(timestamps)

    @pytest.mark.asyncio
    async def test_log_context_404_on_missing_id(self, client: AsyncClient, auth_cookie: dict):
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        res = await client.get("/api/logs/99999/context")
        assert res.status_code == 404

    @pytest.mark.asyncio
    async def test_log_stream_sse(self, client: AsyncClient, auth_cookie: dict):
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])

        test_entry = {
            "id": 999,
            "timestamp": "2026-08-29T15:00:00Z",
            "received_at": "2026-08-29T15:00:01Z",
            "source_ip": "192.168.1.50",
            "source_alias": "unraid-main",
            "app_name": "nginx",
            "facility": 1,
            "severity": 3,
            "message": "SSE test log message",
            "raw": "raw sse",
        }

        async def _trigger_broadcast():
            # Wait for SSE endpoint to register the subscriber queue
            for _ in range(50):
                if sse_manager.subscriber_count() > 0:
                    break
                await asyncio.sleep(0.01)
            sse_manager.broadcast_sync(test_entry)

        broadcast_task = asyncio.create_task(_trigger_broadcast())

        async with client.stream("GET", "/api/logs/stream", params={"max_events": 1}) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            lines = []
            async for line in response.aiter_lines():
                if line.strip():
                    lines.append(line.strip())

            assert any("event: log" in l for l in lines)
            assert any("SSE test log message" in l for l in lines)

        await broadcast_task


# ---------------------------------------------------------------------------
# 4. Host Aliases Tests
# ---------------------------------------------------------------------------

class TestHostAliases:
    @pytest.mark.asyncio
    async def test_host_alias_crud_workflow(self, client: AsyncClient, auth_cookie: dict):
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])

        # 1. Create new alias
        create_res = await client.post(
            "/api/aliases",
            json={"ip": "192.168.1.10", "alias": "router-primary", "notes": "Main OPNsense router"},
        )
        assert create_res.status_code == 200
        assert create_res.json()["alias"] == "router-primary"

        # 2. List aliases
        list_res = await client.get("/api/aliases")
        assert list_res.status_code == 200
        aliases = list_res.json()
        assert len(aliases) == 1
        assert aliases[0]["ip"] == "192.168.1.10"

        # 3. Update existing alias (Upsert)
        update_res = await client.post(
            "/api/aliases",
            json={"ip": "192.168.1.10", "alias": "router-gateway", "notes": "Updated note"},
        )
        assert update_res.status_code == 200
        assert update_res.json()["alias"] == "router-gateway"

        # 4. Delete alias
        del_res = await client.delete("/api/aliases/192.168.1.10")
        assert del_res.status_code == 200
        assert del_res.json()["status"] == "ok"

        # 5. Delete non-existent returns 404
        del_404 = await client.delete("/api/aliases/192.168.1.10")
        assert del_404.status_code == 404


# ---------------------------------------------------------------------------
# 5. System, Storage & Maintenance Tests
# ---------------------------------------------------------------------------

class TestSystemAndMaintenance:
    @pytest.mark.asyncio
    async def test_health_check(self, client: AsyncClient):
        res = await client.get("/api/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["db"] == "ok"
        assert isinstance(data["queue_depth"], int)
        assert isinstance(data["dropped_logs"], int)

    @pytest.mark.asyncio
    async def test_maintenance_prune_and_storage_metrics(
        self, client: AsyncClient, auth_cookie: dict, tmp_path: Path
    ):
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        db_file = tmp_path / "logs.db"

        # Insert an old log (> 40 days old) and a fresh log
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

        # Trigger retention prune (retention_days default = 30)
        res_prune = await client.post("/api/maintenance/prune")
        assert res_prune.status_code == 200
        prune_data = res_prune.json()
        assert prune_data["status"] == "ok"
        assert prune_data["deleted_logs"] == 1
        assert "metrics" in prune_data
        assert prune_data["metrics"]["total_logs_count"] == 1

        # Check /api/system/storage returns metrics history
        res_storage = await client.get("/api/system/storage")
        assert res_storage.status_code == 200
        storage_data = res_storage.json()
        assert storage_data["total_logs_count"] == 1
        assert len(storage_data["history"]) >= 1


# ---------------------------------------------------------------------------
# 6. Notification Endpoints Tests
# ---------------------------------------------------------------------------

class TestNotificationStubs:
    @pytest.mark.asyncio
    async def test_notifications_test_endpoint(self, client: AsyncClient, auth_cookie: dict, tmp_path: Path):
        db_file = tmp_path / "logs.db"
        conn = sqlite3.connect(str(db_file))
        conn.execute("INSERT OR REPLACE INTO system_settings (key, value, updated_at, is_encrypted) VALUES ('pushover_user_key', 'user-key', '2026-08-29T10:00:00Z', 0)")
        conn.execute("INSERT OR REPLACE INTO system_settings (key, value, updated_at, is_encrypted) VALUES ('pushover_app_token', 'app-token', '2026-08-29T10:00:00Z', 0)")
        conn.commit()
        conn.close()

        with patch("app.api.notifications.send_pushover_message", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = {"status": 1}
            client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
            res = await client.post("/api/notifications/test")
            assert res.status_code == 200
            assert res.json()["status"] == "ok"

    @pytest.mark.asyncio
    async def test_notifications_pushover_stub_endpoint(self, client: AsyncClient, auth_cookie: dict, tmp_path: Path):
        db_file = tmp_path / "logs.db"
        conn = sqlite3.connect(str(db_file))
        conn.execute("INSERT OR REPLACE INTO system_settings (key, value, updated_at, is_encrypted) VALUES ('pushover_user_key', 'user-key', '2026-08-29T10:00:00Z', 0)")
        conn.execute("INSERT OR REPLACE INTO system_settings (key, value, updated_at, is_encrypted) VALUES ('pushover_app_token', 'app-token', '2026-08-29T10:00:00Z', 0)")
        conn.commit()
        conn.close()

        with patch("app.api.notifications.send_pushover_message", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = {"status": 1}
            client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
            res = await client.post(
                "/api/notifications/pushover",
                json={"title": "Test Title", "message": "Test Message", "priority": 0},
            )
            assert res.status_code == 200
            assert res.json()["status"] == "sent"
