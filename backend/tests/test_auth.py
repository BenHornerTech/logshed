"""
Tests for Argon2id hashing, session cookies, login rate limiting, admin password change, and admin CLI.
"""

from pathlib import Path
import pytest
import pytest_asyncio
from argon2 import PasswordHasher
from httpx import ASGITransport, AsyncClient

from app.cli import reset_admin
from app.core.config import get_cors_origins
from app.core.migrations import get_connection, run_migrations
from app.core.rate_limiter import login_rate_limiter
from app.core.security import (
    SESSION_COOKIE_NAME,
    create_session_token,
    get_or_create_master_key,
    reset_crypto_cache,
)
from app.main import create_app


@pytest.fixture(autouse=True)
def reset_auth_env(tmp_path: Path, monkeypatch):
    """Reset rate limiter, crypto cache, and database paths for each test."""
    login_rate_limiter.reset()
    reset_crypto_cache()

    db_file = tmp_path / "logs.db"
    key_file = tmp_path / ".secret_key"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("SECRET_KEY_PATH", str(key_file))
    monkeypatch.delenv("LOGSHED_SECRET_KEY", raising=False)

    run_migrations(db_file)
    get_or_create_master_key(key_file)

    yield

    login_rate_limiter.reset()
    reset_crypto_cache()


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


# ===================================================================
# 1. Authentication Endpoints & Session Management
# ===================================================================

class TestAuthentication:

    @pytest.mark.asyncio
    async def test_auth_setup_succeeds_once_and_locks_out(self, client: AsyncClient):
        # First setup should succeed
        res = await client.post("/api/auth/setup", json={"password": "initial_password_123"})
        assert res.status_code == 200
        assert res.json() == {"status": "ok", "detail": None}
        assert SESSION_COOKIE_NAME in res.cookies

        # Verify cookie attributes
        set_cookie = res.headers.get("set-cookie", "")
        assert "session=" in set_cookie
        assert "httponly" in set_cookie.lower()
        assert "samesite=lax" in set_cookie.lower()

        # Second setup attempt must return 403 Forbidden
        res2 = await client.post("/api/auth/setup", json={"password": "another_password_456"})
        assert res2.status_code == 403

    @pytest.mark.asyncio
    async def test_login_success_and_logout(self, client: AsyncClient):
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

        for _ in range(5):
            res = await client.post("/api/auth/login", json={"password": "wrong_password"})
            assert res.status_code == 401

        # 6th attempt must trigger 429 Too Many Requests
        res_blocked = await client.post("/api/auth/login", json={"password": "wrong_password"})
        assert res_blocked.status_code == 429
        assert "Too many failed login attempts" in res_blocked.json()["detail"]

        # Even with correct password, blocked until window clears
        res_blocked_correct = await client.post("/api/auth/login", json={"password": "correct_password"})
        assert res_blocked_correct.status_code == 429

    @pytest.mark.asyncio
    async def test_auth_status_endpoint(self, client: AsyncClient, auth_cookie: dict):
        # Before setup
        res = await client.get("/api/auth/status")
        assert res.status_code == 200
        assert res.json() == {"setup_required": True, "authenticated": False}

        # After setup
        await client.post("/api/auth/setup", json={"password": "secure_password"})
        res2 = await client.get("/api/auth/status")
        assert res2.status_code == 200
        assert res2.json()["setup_required"] is False
        assert res2.json()["authenticated"] is True

        # Fresh client without cookie
        transport = ASGITransport(app=create_app())
        async with AsyncClient(transport=transport, base_url="http://test") as clean_client:
            res3 = await clean_client.get("/api/auth/status")
            assert res3.json() == {"setup_required": False, "authenticated": False}

            clean_client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
            res4 = await clean_client.get("/api/auth/status")
            assert res4.json() == {"setup_required": False, "authenticated": True}

    @pytest.mark.asyncio
    async def test_unauthenticated_requests_rejected_on_protected_endpoints(self, client: AsyncClient):
        endpoints = [
            ("GET", "/api/logs"),
            ("GET", "/api/logs/1/context"),
            ("GET", "/api/settings"),
            ("POST", "/api/settings"),
            ("GET", "/api/aliases"),
            ("POST", "/api/aliases"),
            ("POST", "/api/maintenance/prune"),
            ("GET", "/api/system/storage"),
        ]

        for method, endpoint in endpoints:
            if method == "GET":
                res = await client.get(endpoint)
            else:
                res = await client.post(endpoint, json={})
            assert res.status_code == 401, f"{method} {endpoint} did not return 401"

    @pytest.mark.asyncio
    async def test_cookie_secure_auto_detection_and_env_override(self, client: AsyncClient, monkeypatch):
        # Plain HTTP without proxy header
        res_http = await client.post("/api/auth/setup", json={"password": "secure_pwd_123"})
        assert res_http.status_code == 200
        set_cookie_http = res_http.headers.get("set-cookie", "").lower()
        cookie_parts_http = [p.strip() for p in set_cookie_http.split(";")]
        assert "secure" not in cookie_parts_http

        # Proxy with x-forwarded-proto: https -> secure=True
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as https_client:
            res_https = await https_client.post(
                "/api/auth/login",
                json={"password": "secure_pwd_123"},
                headers={"x-forwarded-proto": "https"},
            )
            assert res_https.status_code == 200
            set_cookie_https = res_https.headers.get("set-cookie", "").lower()
            cookie_parts_https = [p.strip() for p in set_cookie_https.split(";")]
            assert "secure" in cookie_parts_https

        # COOKIE_SECURE environment variable override
        monkeypatch.setenv("COOKIE_SECURE", "true")
        async with AsyncClient(transport=transport, base_url="http://test") as env_client:
            res_env = await env_client.post("/api/auth/login", json={"password": "secure_pwd_123"})
            assert res_env.status_code == 200
            set_cookie_env = res_env.headers.get("set-cookie", "").lower()
            cookie_parts_env = [p.strip() for p in set_cookie_env.split(";")]
            assert "secure" in cookie_parts_env


# ===================================================================
# 2. CORS Configuration
# ===================================================================

class TestCorsConfiguration:

    def test_default_cors_origins(self, monkeypatch):
        monkeypatch.delenv("CORS_ORIGINS", raising=False)
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        monkeypatch.delenv("DEBUG", raising=False)
        assert get_cors_origins() == []

    def test_development_cors_origins(self, monkeypatch):
        monkeypatch.delenv("CORS_ORIGINS", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "development")
        origins = get_cors_origins()
        assert "http://localhost:5173" in origins
        assert "http://localhost:8080" in origins

    def test_debug_cors_origins(self, monkeypatch):
        monkeypatch.delenv("CORS_ORIGINS", raising=False)
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        monkeypatch.setenv("DEBUG", "true")
        origins = get_cors_origins()
        assert "http://localhost:5173" in origins

    def test_custom_cors_origins(self, monkeypatch):
        monkeypatch.setenv("CORS_ORIGINS", "http://192.168.1.50:8080, https://logs.example.com")
        origins = get_cors_origins()
        assert origins == ["http://192.168.1.50:8080", "https://logs.example.com"]


# ===================================================================
# 3. Authenticated Admin Password Change
# ===================================================================

class TestAdminPasswordChange:

    @pytest.mark.asyncio
    async def test_change_password_success_and_login_with_new_pwd(self, client: AsyncClient):
        # Setup initial admin
        await client.post("/api/auth/setup", json={"password": "SuperSecretAdminPassword123!"})

        # Change password while authenticated
        change_res = await client.post(
            "/api/auth/password",
            json={
                "current_password": "SuperSecretAdminPassword123!",
                "new_password": "BrandNewPassword987!",
            },
        )
        assert change_res.status_code == 200
        assert change_res.json()["status"] == "ok"

        # Try logging in with old password (must fail)
        transport = ASGITransport(app=create_app())
        async with AsyncClient(transport=transport, base_url="http://test") as unauth_client:
            old_login = await unauth_client.post(
                "/api/auth/login",
                json={"password": "SuperSecretAdminPassword123!"},
            )
            assert old_login.status_code == 401

            # Try logging in with new password (must succeed)
            new_login = await unauth_client.post(
                "/api/auth/login",
                json={"password": "BrandNewPassword987!"},
            )
            assert new_login.status_code == 200
            assert SESSION_COOKIE_NAME in new_login.cookies


# ===================================================================
# 4. Admin CLI Password Reset
# ===================================================================

class TestCLIPasswordReset:

    def test_reset_admin_on_empty_table(self, tmp_path: Path):
        db_path = tmp_path / "cli_empty.db"
        run_migrations(db_path)
        reset_admin("newpassword123", str(db_path))

        conn = get_connection(db_path)
        row = conn.execute(
            "SELECT id, password_hash, created_at, updated_at FROM admin_auth"
        ).fetchone()
        conn.close()

        assert row is not None
        assert row[0] == 1
        assert row[1].startswith("$argon2id$")

        ph = PasswordHasher()
        assert ph.verify(row[1], "newpassword123")

    def test_reset_admin_on_existing_row(self, tmp_path: Path):
        db_path = tmp_path / "cli_existing.db"
        run_migrations(db_path)

        ph = PasswordHasher()
        old_hash = ph.hash("oldpassword")
        conn = get_connection(db_path)
        conn.execute(
            "INSERT INTO admin_auth (id, password_hash, created_at, updated_at) "
            "VALUES (1, ?, '2024-01-01T00:00:00', '2024-01-01T00:00:00')",
            (old_hash,),
        )
        conn.commit()
        conn.close()

        reset_admin("newpassword456", str(db_path))

        conn = get_connection(db_path)
        row = conn.execute(
            "SELECT password_hash, created_at, updated_at FROM admin_auth WHERE id=1"
        ).fetchone()
        conn.close()

        assert row is not None
        new_hash = row[0]
        assert new_hash.startswith("$argon2id$")
        with pytest.raises(Exception):
            ph.verify(new_hash, "oldpassword")
        assert ph.verify(new_hash, "newpassword456")
        assert row[1] == "2024-01-01T00:00:00"
        assert row[2] != "2024-01-01T00:00:00"

    def test_reset_admin_argon2id_algorithm(self, tmp_path: Path):
        db_path = tmp_path / "cli_algo.db"
        run_migrations(db_path)
        reset_admin("testpass", str(db_path))

        conn = get_connection(db_path)
        row = conn.execute(
            "SELECT password_hash FROM admin_auth WHERE id=1"
        ).fetchone()
        conn.close()

        assert row is not None
        assert "$argon2id$" in row[0]

    def test_reset_admin_only_one_row(self, tmp_path: Path):
        db_path = tmp_path / "cli_onerow.db"
        run_migrations(db_path)
        reset_admin("pass1", str(db_path))
        reset_admin("pass2", str(db_path))
        reset_admin("pass3", str(db_path))

        conn = get_connection(db_path)
        count = conn.execute("SELECT COUNT(*) FROM admin_auth").fetchone()[0]
        conn.close()

        assert count == 1
