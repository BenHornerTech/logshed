"""
Phase 4 Verification Test Suite for LogShed.
Tests FastAPI SPA static serving, AI preview/analyze/audit endpoints, same-host constraints, and password changing.
"""

import os
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core import pipeline as pipeline_mod
from app.core.config import get_secret_key_path
from app.core.migrations import run_migrations
from app.core.rate_limiter import login_rate_limiter
from app.core.security import (
    SESSION_COOKIE_NAME,
    create_session_token,
    get_or_create_master_key,
    hash_password,
    reset_crypto_cache,
)
from app.core.sse import sse_manager
from app.main import create_app


@pytest.fixture(autouse=True)
def reset_globals(tmp_path: Path, monkeypatch):
    """Reset queues, rate limiter, encryption keys, and environment for each test."""
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


@pytest.fixture
def populated_db(tmp_path: Path):
    db_file = tmp_path / "logs.db"
    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()

    # Populate admin auth
    pwd_hash = hash_password("SuperSecretAdminPassword123!")
    cursor.execute(
        "INSERT INTO admin_auth (id, password_hash, created_at, updated_at) VALUES (1, ?, '2026-08-29T10:00:00Z', '2026-08-29T10:00:00Z')",
        (pwd_hash,),
    )

    # Populate host aliases
    cursor.execute(
        "INSERT INTO host_aliases (ip, alias, notes, created_at) VALUES ('192.168.1.1', 'router', 'gateway', '2026-08-29T10:00:00Z')"
    )

    # Populate system settings
    cursor.execute(
        "INSERT INTO system_settings (key, value, updated_at, is_encrypted) VALUES ('ai_provider', 'gemini', '2026-08-29T10:00:00Z', 0)"
    )
    cursor.execute(
        "INSERT INTO system_settings (key, value, updated_at, is_encrypted) VALUES ('ai_model', 'gemini-2.5-flash', '2026-08-29T10:00:00Z', 0)"
    )
    cursor.execute(
        "INSERT INTO system_settings (key, value, updated_at, is_encrypted) VALUES ('ai_api_key', 'test-api-key', '2026-08-29T10:00:00Z', 0)"
    )

    # Populate test logs
    logs = [
        ("2026-08-29T12:00:01Z", "2026-08-29T12:00:01Z", "192.168.1.1", "router", "dnsmasq", 1, 3, "query error api_key=supersecret12345", "raw1"),
        ("2026-08-29T12:00:02Z", "2026-08-29T12:00:02Z", "192.168.1.1", "router", "dnsmasq", 1, 6, "normal message", "raw2"),
        ("2026-08-29T12:00:03Z", "2026-08-29T12:00:03Z", "192.168.1.50", "proxmox-01", "pve-ha", 1, 3, "quorum lost", "raw3"),
    ]
    cursor.executemany(
        """
        INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        logs,
    )
    conn.commit()
    conn.close()
    return str(db_file)


@pytest.fixture
def auth_cookie() -> dict[str, str]:
    token = create_session_token(user_id=1)
    return {SESSION_COOKIE_NAME: token}


@pytest_asyncio.fixture
async def client():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def auth_client(auth_cookie):
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies=auth_cookie) as ac:
        yield ac


class TestSpaStaticServing:
    @pytest.mark.asyncio
    async def test_spa_serves_index_html_for_frontend_routes(self, populated_db, client):
        res = await client.get("/stream")
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]
        assert "<!doctype html>" in res.text or "<html" in res.text

    @pytest.mark.asyncio
    async def test_api_404_does_not_serve_spa_html(self, populated_db, client):
        res = await client.get("/api/nonexistent_route")
        assert res.status_code == 404
        assert res.headers["content-type"].startswith("application/json")


class TestAiEndpoints:
    @pytest.mark.asyncio
    async def test_ai_preview_sanitizes_secrets(self, populated_db, auth_client):
        res = await auth_client.post(
            "/api/ai/preview",
            json={"log_ids": [1, 2]},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["source_alias"] == "router"
        assert data["log_count"] == 2
        assert "[REDACTED]" in data["sanitized_prompt"]
        assert "supersecret12345" not in data["sanitized_prompt"]

    @pytest.mark.asyncio
    async def test_ai_preview_rejects_mixed_host_selection(self, populated_db, auth_client):
        # Log 1 is 'router', Log 3 is 'proxmox-01'
        res = await auth_client.post(
            "/api/ai/preview",
            json={"log_ids": [1, 3]},
        )
        assert res.status_code == 400
        assert "Selected logs must share the same host alias" in res.json()["detail"]

    @pytest.mark.asyncio
    async def test_ai_analyze_and_audit_workflow(self, populated_db, auth_client):
        # Run analysis with mock
        with patch("app.api.ai.execute_ai_analysis", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = (
                "Analysis for 2 log entries from router (dnsmasq).",
                "The log stream indicates potential service configuration errors.",
                "1. Check container service status.\n2. Review network.",
                "Raw LLM response",
                "Full prompt sent with logs and metadata",
                150,
                50,
                0,
                200,
            )
            res = await auth_client.post(
                "/api/ai/analyze",
                json={
                    "log_ids": [1, 2],
                    "user_context": "Testing recent router changes",
                    "model": "gemini-2.5-flash",
                },
            )
            assert res.status_code == 200
            data = res.json()
            assert "summary" in data
            assert "root_cause" in data
            assert "remediation" in data
            assert data["audit_id"] is not None

        # Retrieve audit log
        audit_res = await auth_client.get("/api/ai/audit?limit=10")
        assert audit_res.status_code == 200
        audit_data = audit_res.json()
        assert audit_data["total"] >= 1
        entry = audit_data["items"][0]
        assert entry["source_alias"] == "router"
        assert entry["user_context"] == "Testing recent router changes"


class TestAdminPasswordChange:
    @pytest.mark.asyncio
    async def test_change_password_success_and_login_with_new_pwd(self, populated_db, auth_client, client):
        # Change password
        change_res = await auth_client.post(
            "/api/auth/password",
            json={
                "current_password": "SuperSecretAdminPassword123!",
                "new_password": "BrandNewPassword987!",
            },
        )
        assert change_res.status_code == 200
        assert change_res.json()["status"] == "ok"

        # Try logging in with old password (should fail)
        old_login = await client.post("/api/auth/login", json={"password": "SuperSecretAdminPassword123!"})
        assert old_login.status_code == 401

        # Try logging in with new password (should succeed)
        new_login = await client.post("/api/auth/login", json={"password": "BrandNewPassword987!"})
        assert new_login.status_code == 200
