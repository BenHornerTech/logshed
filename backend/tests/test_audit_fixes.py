"""
Tests for low severity audit fixes (L1 through L6).
"""

import asyncio
from collections import OrderedDict
import concurrent.futures
import datetime
import os
from pathlib import Path
import sqlite3
import pytest
from httpx import ASGITransport, AsyncClient

from app.collectors.syslog import (
    _alias_cache,
    _alias_cache_lock,
    _ALIAS_CACHE_MAX_SIZE,
    resolve_alias,
)
from app.core.config import get_cors_origins
from app.core.migrations import get_connection, run_migrations
from app.core import pipeline as pipeline_mod
from app.core.pipeline import (
    get_dropped_count,
    increment_dropped_count,
)
from app.core.security import (
    SESSION_COOKIE_NAME,
    create_session_token,
    hash_password,
)
from app.main import _supervise_worker, create_app
from app.services.retention import PruneWorker
from app.services.storage_metrics import StorageMetricsWorker


# ---------------------------------------------------------------------------
# L1: Thread-safe dropped logs counter
# ---------------------------------------------------------------------------
class TestL1DropCounterThreadSafety:
    def test_concurrent_increments(self):
        pipeline_mod._dropped_logs_total = 0
        num_threads = 10
        increments_per_thread = 500

        def _worker():
            for _ in range(increments_per_thread):
                increment_dropped_count(1)

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(_worker) for _ in range(num_threads)]
            concurrent.futures.wait(futures)

        assert get_dropped_count() == num_threads * increments_per_thread


# ---------------------------------------------------------------------------
# L2: CORS configuration
# ---------------------------------------------------------------------------
class TestL2CorsConfiguration:
    def test_default_cors_origins(self, monkeypatch):
        monkeypatch.delenv("CORS_ORIGINS", raising=False)
        origins = get_cors_origins()
        assert "http://localhost:5173" in origins
        assert "http://localhost:8080" in origins

    def test_custom_cors_origins(self, monkeypatch):
        monkeypatch.setenv("CORS_ORIGINS", "http://192.168.1.50:8080, https://logs.example.com")
        origins = get_cors_origins()
        assert origins == ["http://192.168.1.50:8080", "https://logs.example.com"]


# ---------------------------------------------------------------------------
# L3: Native FTS5 syntax and error recovery
# ---------------------------------------------------------------------------
class TestL3FtsSyntaxSupport:
    @pytest.fixture
    def app_with_db(self, tmp_path: Path, monkeypatch):
        db_file = tmp_path / "logs.db"
        secret_file = tmp_path / ".secret_key"
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.setenv("DB_PATH", str(db_file))
        monkeypatch.setenv("SECRET_KEY_PATH", str(secret_file))

        run_migrations(db_file)
        app = create_app()
        return app, db_file

    @pytest.fixture
    def auth_cookie(self, app_with_db) -> dict:
        _, db_file = app_with_db
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with get_connection(db_file) as conn:
            conn.execute(
                "INSERT INTO admin_auth (id, password_hash, created_at, updated_at) VALUES (1, ?, ?, ?)",
                (hash_password("adminpass123"), now, now),
            )
            conn.commit()
        token = create_session_token()
        return {SESSION_COOKIE_NAME: token}

    @pytest.mark.asyncio
    async def test_native_fts5_syntax_and_syntax_error_fallback(
        self, app_with_db, auth_cookie: dict
    ):
        app, db_file = app_with_db
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])

            # Seed entries
            entries = [
                ("2026-08-30T10:00:00Z", "2026-08-30T10:00:01Z", "10.0.0.1", "server1", "nginx", 1, 3, "Connection refused to backend upstream", "<11>nginx: Connection refused to backend upstream"),
                ("2026-08-30T10:01:00Z", "2026-08-30T10:01:01Z", "10.0.0.1", "server1", "nginx", 1, 3, "Connection timeout to redis", "<11>nginx: Connection timeout to redis"),
                ("2026-08-30T10:02:00Z", "2026-08-30T10:02:01Z", "10.0.0.2", "server2", "auth", 1, 2, "Authentication error: password mismatch", "<10>auth: Authentication error: password mismatch"),
            ]
            with get_connection(db_file) as conn:
                conn.executemany(
                    """INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    entries,
                )
                conn.commit()

            # 1. Column filter syntax: app_name:auth
            res_col = await client.get("/api/logs", params={"query": "app_name:auth"})
            assert res_col.status_code == 200
            data_col = res_col.json()
            assert data_col["total"] == 1
            assert data_col["logs"][0]["app_name"] == "auth"

            # 2. Boolean operators: "Connection NOT timeout"
            res_bool = await client.get("/api/logs", params={"query": "Connection NOT timeout"})
            assert res_bool.status_code == 200
            data_bool = res_bool.json()
            assert data_bool["total"] == 1
            assert "refused" in data_bool["logs"][0]["message"]

            # 3. Wildcard prefix search: "refus*"
            res_prefix = await client.get("/api/logs", params={"query": "refus*"})
            assert res_prefix.status_code == 200
            assert res_prefix.json()["total"] == 1

            # 4. Malformed syntax (unbalanced quote): fallback should execute without 500 error
            res_bad = await client.get("/api/logs", params={"query": 'Connection "refused'})
            assert res_bad.status_code == 200
            assert res_bad.json()["total"] == 1


# ---------------------------------------------------------------------------
# L4 & L5: Alias cache bounding and get_connection usage
# ---------------------------------------------------------------------------
class TestL4AndL5AliasCache:
    def test_alias_cache_lru_bounding(self, tmp_path: Path):
        db_file = tmp_path / "logs.db"
        run_migrations(db_file)

        with _alias_cache_lock:
            _alias_cache.clear()

        # Insert 1050 items
        for i in range(1050):
            resolve_alias(f"192.168.100.{i}", db_file)

        with _alias_cache_lock:
            assert len(_alias_cache) <= _ALIAS_CACHE_MAX_SIZE
            # The earliest inserted item (0) should have been evicted
            assert "192.168.100.0" not in _alias_cache
            # The most recent item should be present
            assert "192.168.100.1049" in _alias_cache

    def test_alias_resolution_persisted(self, tmp_path: Path):
        db_file = tmp_path / "logs.db"
        run_migrations(db_file)
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        with get_connection(db_file) as conn:
            conn.execute(
                "INSERT INTO host_aliases (ip, alias, created_at) VALUES ('192.168.1.99', 'truenas-core', ?)",
                (now,),
            )
            conn.commit()

        with _alias_cache_lock:
            _alias_cache.clear()

        resolved = resolve_alias("192.168.1.99", db_file)
        assert resolved == "truenas-core"


# ---------------------------------------------------------------------------
# L6: Worker backoff and exception isolation
# ---------------------------------------------------------------------------
class TestL6WorkerBackoff:
    @pytest.mark.asyncio
    async def test_storage_metrics_worker_exception_backoff(self, tmp_path: Path):
        # Point to invalid path to trigger error in record_metrics
        invalid_path = tmp_path / "non_existent" / "db.sqlite"
        worker = StorageMetricsWorker(invalid_path)
        
        task = asyncio.create_task(worker.run())
        # Let it run briefly and trigger error branch
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
            # Second attempt succeeds and waits
            await asyncio.sleep(0.5)

        task = asyncio.create_task(_supervise_worker(failing_worker, "TestWorker"))
        await asyncio.sleep(1.2)  # Allow 1s initial backoff to elapse
        assert attempts >= 2
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
