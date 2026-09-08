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
    async def test_login_rate_limiting_untrusted_spoofed_xff_does_not_bypass(self):
        """
        Verify that an attacker on an untrusted IP cycling X-Forwarded-For cannot
        bypass login rate limits. The server must use the peer IP and block on the 6th attempt.
        """
        app = create_app()
        # Untrusted origin connecting directly
        untrusted_transport = ASGITransport(app=app, client=("198.51.100.5", 50000))
        async with AsyncClient(transport=untrusted_transport, base_url="http://test") as untrusted_client:
            await untrusted_client.post("/api/auth/setup", json={"password": "real_password"})

            # Attacker cycles different X-Forwarded-For headers to try to bypass rate limiting
            for i in range(5):
                res = await untrusted_client.post(
                    "/api/auth/login",
                    json={"password": "wrong_password"},
                    headers={"X-Forwarded-For": f"10.0.0.{i+1}"},
                )
                assert res.status_code == 401

            # 6th attempt with yet another X-Forwarded-For must be blocked by rate limit
            res_blocked = await untrusted_client.post(
                "/api/auth/login",
                json={"password": "wrong_password"},
                headers={"X-Forwarded-For": "10.0.0.99"},
            )
            assert res_blocked.status_code == 429
            assert "Too many failed login attempts" in res_blocked.json()["detail"]

    @pytest.mark.asyncio
    async def test_login_rate_limiting_untrusted_spoofed_xff_does_not_lockout_victim(self):
        """
        Verify that an attacker on an untrusted IP spoofing an admin/victim IP in X-Forwarded-For
        does not lock out the victim.
        """
        app = create_app()
        untrusted_transport = ASGITransport(app=app, client=("198.51.100.5", 50000))
        victim_transport = ASGITransport(app=app, client=("203.0.113.50", 50000))

        async with AsyncClient(transport=untrusted_transport, base_url="http://test") as attacker_client, \
                   AsyncClient(transport=victim_transport, base_url="http://test") as victim_client:
            await attacker_client.post("/api/auth/setup", json={"password": "secure_admin_pass"})

            # Attacker sends failed attempts claiming to be the victim in X-Forwarded-For
            for _ in range(5):
                res = await attacker_client.post(
                    "/api/auth/login",
                    json={"password": "wrong_password"},
                    headers={"X-Forwarded-For": "203.0.113.50"},
                )
                assert res.status_code == 401

            # Attacker should be rate limited on their own IP
            attacker_res = await attacker_client.post(
                "/api/auth/login",
                json={"password": "wrong_password"},
                headers={"X-Forwarded-For": "203.0.113.50"},
            )
            assert attacker_res.status_code == 429

            # Victim logging in with their actual credentials must NOT be locked out
            victim_res = await victim_client.post(
                "/api/auth/login",
                json={"password": "secure_admin_pass"},
            )
            assert victim_res.status_code == 200

    @pytest.mark.asyncio
    async def test_login_trusted_proxies_parses_rightmost_untrusted_hop(self, monkeypatch):
        """
        When TRUSTED_PROXIES is configured, the server parses the rightmost untrusted hop
        from X-Forwarded-For to attribute rate limiting.
        """
        monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.1, 10.0.0.2")
        app = create_app()
        # Direct connection comes from trusted proxy 10.0.0.1
        proxy_transport = ASGITransport(app=app, client=("10.0.0.1", 50000))
        async with AsyncClient(transport=proxy_transport, base_url="http://test") as proxy_client:
            await proxy_client.post("/api/auth/setup", json={"password": "proxy_password"})

            # XFF chain: [spoofed_ip, untrusted_client_ip, trusted_proxy_ip]
            # Rightmost untrusted hop is 203.0.113.88
            xff = "192.0.2.1, 203.0.113.88, 10.0.0.2"

            for _ in range(5):
                res = await proxy_client.post(
                    "/api/auth/login",
                    json={"password": "wrong_password"},
                    headers={"X-Forwarded-For": xff},
                )
                assert res.status_code == 401

            # 6th attempt from same untrusted client IP is rate limited
            res_blocked = await proxy_client.post(
                "/api/auth/login",
                json={"password": "wrong_password"},
                headers={"X-Forwarded-For": xff},
            )
            assert res_blocked.status_code == 429

            # A different client coming through the same proxy chain is NOT rate limited
            res_other = await proxy_client.post(
                "/api/auth/login",
                json={"password": "wrong_password"},
                headers={"X-Forwarded-For": "203.0.113.99, 10.0.0.2"},
            )
            assert res_other.status_code == 401

    @pytest.mark.asyncio
    async def test_login_docker_bridge_trusted_proxy_gateway(self, monkeypatch):
        """
        When TRUSTED_PROXIES is unset and TRUST_DOCKER_PROXIES is enabled,
        Docker bridge proxy gateways (e.g. 172.17.0.1 in 172.16.0.0/12) are trusted
        so they do not mask all incoming user traffic under one IP.
        """
        monkeypatch.delenv("TRUSTED_PROXIES", raising=False)
        monkeypatch.setenv("TRUST_DOCKER_PROXIES", "true")

        app = create_app()
        # Direct connection comes from Docker gateway 172.17.0.1
        proxy_transport = ASGITransport(app=app, client=("172.17.0.1", 50000))
        async with AsyncClient(transport=proxy_transport, base_url="http://test") as proxy_client:
            await proxy_client.post("/api/auth/setup", json={"password": "gateway_password"})

            # Client 203.0.113.88 sends 5 failed attempts through 172.17.0.1
            for _ in range(5):
                res = await proxy_client.post(
                    "/api/auth/login",
                    json={"password": "wrong_password"},
                    headers={"X-Forwarded-For": "203.0.113.88"},
                )
                assert res.status_code == 401

            # 6th attempt from 203.0.113.88 is rate-limited
            res_blocked = await proxy_client.post(
                "/api/auth/login",
                json={"password": "wrong_password"},
                headers={"X-Forwarded-For": "203.0.113.88"},
            )
            assert res_blocked.status_code == 429

            # A different client coming through the exact same gateway is NOT rate limited
            res_other = await proxy_client.post(
                "/api/auth/login",
                json={"password": "wrong_password"},
                headers={"X-Forwarded-For": "203.0.113.99"},
            )
            assert res_other.status_code == 401

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

    @pytest.mark.asyncio
    async def test_session_issued_prior_to_password_change_rejected(self, client: AsyncClient):
        # 1. Setup admin
        await client.post("/api/auth/setup", json={"password": "InitialSecurePass123!"})
        initial_token = client.cookies.get(SESSION_COOKIE_NAME)
        assert initial_token is not None

        # Verify initial session token works on protected endpoint
        res_before = await client.get("/api/settings")
        assert res_before.status_code == 200

        # Wait briefly so that updated_at timestamp in admin_auth is strictly greater
        import asyncio
        await asyncio.sleep(1.05)

        # 2. Change password while authenticated
        change_res = await client.post(
            "/api/auth/password",
            json={
                "current_password": "InitialSecurePass123!",
                "new_password": "UpdatedPassword456!",
            },
        )
        assert change_res.status_code == 200

        # 3. Request using session token issued prior to password change must be rejected
        transport = ASGITransport(app=create_app())
        async with AsyncClient(transport=transport, base_url="http://test") as old_client:
            old_client.cookies.set(SESSION_COOKIE_NAME, initial_token)
            res_after = await old_client.get("/api/settings")
            assert res_after.status_code == 401
            assert "Session expired due to password change" in res_after.json()["detail"]

        # 4. Request using a newly created session (logged in after password update) must succeed
        async with AsyncClient(transport=transport, base_url="http://test") as new_client:
            login_res = await new_client.post(
                "/api/auth/login",
                json={"password": "UpdatedPassword456!"},
            )
            assert login_res.status_code == 200
            res_new_session = await new_client.get("/api/settings")
            assert res_new_session.status_code == 200


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

    @pytest.mark.asyncio
    async def test_password_max_length_enforced(self, client: AsyncClient):
        """Passwords exceeding 128 characters are rejected with 422 Unprocessable Entity."""
        too_long = "A" * 129

        # Setup
        res_setup = await client.post("/api/auth/setup", json={"password": too_long})
        assert res_setup.status_code == 422

        # Login
        res_login = await client.post("/api/auth/login", json={"password": too_long})
        assert res_login.status_code == 422

        # Setup valid password to authenticate for password change
        await client.post("/api/auth/setup", json={"password": "valid_initial_pwd"})

        # Password Change
        res_change = await client.post(
            "/api/auth/password",
            json={"current_password": "valid_initial_pwd", "new_password": too_long},
        )
        assert res_change.status_code == 422

