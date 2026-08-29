"""
Phase 2 verification tests for Homelab Log Hub.

Covers:
  - Secret redaction (sanitizer.py) — API keys, Bearer tokens, passwords,
    JWTs, AWS keys, private keys, connection strings, Pushover tokens.
  - Docker log stream assembler — multiline stack traces merged via the
    shared KeyedMultilineAssembler with stream_key="docker:{container_id}".
  - CLI password reset — UPSERTs admin_auth.password_hash with a valid
    Argon2id hash on both empty and populated admin_auth tables.
  - Docker host parsing for unix and tcp DOCKER_HOST values.
"""

import asyncio
import datetime
import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from argon2 import PasswordHasher

from app.core.migrations import get_connection, run_migrations
from app.core import pipeline as pipeline_mod
from app.core.pipeline import (
    KeyedMultilineAssembler,
    _is_continuation,
    get_dropped_count,
    get_queue,
)
from app.core.sanitizer import sanitize, REDACTED
from app.cli import reset_admin
from app.collectors.docker_collector import (
    _parse_docker_host,
    _parse_docker_log_line,
    _make_log_entry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Create a fresh migrated database and return its path."""
    p = tmp_path / "logs.db"
    run_migrations(p)
    return p


@pytest.fixture(autouse=True)
def reset_queue():
    """Reset the global queue and drop counter between tests."""
    pipeline_mod._log_queue = None
    pipeline_mod._dropped_logs_total = 0
    yield
    pipeline_mod._log_queue = None
    pipeline_mod._dropped_logs_total = 0


def _make_entry(**overrides) -> dict:
    """Helper to build a log-entry dict with sane defaults."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    base = {
        "timestamp": now,
        "received_at": now,
        "source_ip": "docker",
        "source_alias": "unraid-docker",
        "app_name": "test-container",
        "facility": 1,
        "severity": 6,
        "message": "hello world",
        "raw": "hello world",
    }
    base.update(overrides)
    return base


# ===================================================================
# 1. Secret redaction (sanitizer.py)
# ===================================================================

class TestSanitizer:

    def test_bearer_token_redacted(self):
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xxx.yyy"
        result = sanitize(text)
        assert "eyJ" not in result
        assert REDACTED in result
        assert "Authorization" in result

    def test_basic_auth_redacted(self):
        text = "Authorization: Basic dXNlcjpwYXNzd29yZA=="
        result = sanitize(text)
        assert "dXNlcjpwYXNzd29yZA==" not in result
        assert REDACTED in result

    def test_api_key_redacted(self):
        text = "api_key=sk-1234567890abcdef1234567890abcdef"
        result = sanitize(text)
        assert "sk-1234567890abcdef" not in result
        assert REDACTED in result

    def test_api_key_colon_format(self):
        text = "API-KEY: my_super_secret_key_value_12345678"
        result = sanitize(text)
        assert "my_super_secret_key_value_12345678" not in result
        assert REDACTED in result

    def test_password_field_redacted(self):
        text = "password=MyS3cretP@ss!"
        result = sanitize(text)
        assert "MyS3cretP@ss!" not in result
        assert REDACTED in result

    def test_password_colon_format(self):
        text = "password: hunter2"
        result = sanitize(text)
        assert "hunter2" not in result
        assert REDACTED in result

    def test_token_field_redacted(self):
        text = 'access_token=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'
        result = sanitize(text)
        assert "ghp_" not in result
        assert REDACTED in result

    def test_jwt_standalone_redacted(self):
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        text = f"Token is {jwt}"
        result = sanitize(text)
        assert "eyJhbGciOiJIUzI1NiI" not in result
        assert REDACTED in result

    def test_aws_access_key_redacted(self):
        text = "AWS key is AKIAIOSFODNN7EXAMPLE"
        result = sanitize(text)
        assert "AKIAIOSFODNN7EXAMPLE" not in result
        assert REDACTED in result

    def test_aws_secret_key_redacted(self):
        text = "aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY12"
        result = sanitize(text)
        assert "wJalrXUtnFEMI" not in result
        assert REDACTED in result

    def test_private_key_redacted(self):
        text = """-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA0Z3VS5JJcds3xfnNkGFOQCPO...
-----END RSA PRIVATE KEY-----"""
        result = sanitize(text)
        assert "MIIEpAIBAAKCAQEA0Z3VS5JJcds3xfnNkGFOQCPO" not in result
        assert REDACTED in result

    def test_connection_string_password_redacted(self):
        text = "postgres://admin:s3cret_pass@db.example.com:5432/mydb"
        result = sanitize(text)
        assert "s3cret_pass" not in result
        assert REDACTED in result
        assert "db.example.com" in result

    def test_pushover_token_redacted(self):
        text = "pushover_app_token=azGDORePK8gMaC0QOYAMyEEuzJnyUi"
        result = sanitize(text)
        assert "azGDORePK8gMaC0QOYAMyEEuzJnyUi" not in result
        assert REDACTED in result

    def test_normal_text_unchanged(self):
        text = "INFO: Container nginx started successfully on port 8080"
        result = sanitize(text)
        assert result == text

    def test_sanitize_list(self):
        lines = [
            "Normal log line",
            "api_key=secret12345678",
            "password=hunter2",
        ]
        result = sanitize(lines)
        assert isinstance(result, list)
        assert len(result) == 3
        assert result[0] == "Normal log line"
        assert "secret12345678" not in result[1]
        assert "hunter2" not in result[2]

    def test_multiple_secrets_in_one_line(self):
        text = "Authorization: Bearer tok123abc password=mypass api_key=sk-abc12345678"
        result = sanitize(text)
        assert "tok123abc" not in result
        assert "mypass" not in result
        assert "sk-abc12345678" not in result

    def test_x_api_key_header(self):
        text = "X-API-Key: my-secret-api-key-12345"
        result = sanitize(text)
        assert "my-secret-api-key-12345" not in result
        assert REDACTED in result


# ===================================================================
# 2. Docker log stream multiline assembly
# ===================================================================

class TestDockerMultilineAssembly:

    @pytest.mark.asyncio
    async def test_docker_stream_key_format(self):
        """Docker stream keys should use 'docker:{container_id}' format."""
        asm = KeyedMultilineAssembler()
        container_id = "abc123def456"
        stream_key = f"docker:{container_id}"

        entry = _make_entry(message="Java exception")
        await asm.feed(stream_key, entry)
        await asyncio.sleep(0.3)

        q = get_queue()
        assert not q.empty()
        item = q.get_nowait()
        assert item["message"] == "Java exception"

    @pytest.mark.asyncio
    async def test_docker_java_stacktrace_merging(self):
        """Java stack traces from Docker should be merged into one log entry."""
        asm = KeyedMultilineAssembler()
        stream_key = "docker:container123"

        await asm.feed(stream_key, _make_entry(
            message="Exception in thread \"main\" java.lang.NullPointerException",
            severity=3,
        ))
        await asm.feed(stream_key, _make_entry(
            message="  at com.example.Main.process(Main.java:42)",
            severity=6,
        ))
        await asm.feed(stream_key, _make_entry(
            message="  at com.example.Main.main(Main.java:10)",
            severity=6,
        ))
        await asm.feed(stream_key, _make_entry(
            message="Caused by: java.io.IOException: Connection refused",
            severity=4,
        ))
        await asm.feed(stream_key, _make_entry(
            message="  at java.net.Socket.connect(Socket.java:591)",
            severity=6,
        ))

        await asyncio.sleep(0.3)

        q = get_queue()
        assert not q.empty()
        item = q.get_nowait()

        # All lines should be merged
        assert "NullPointerException" in item["message"]
        assert "com.example.Main.process" in item["message"]
        assert "Caused by:" in item["message"]
        assert "java.net.Socket.connect" in item["message"]
        # Severity should be min (most severe)
        assert item["severity"] == 3

    @pytest.mark.asyncio
    async def test_docker_python_traceback_merging(self):
        """Python tracebacks from Docker should be merged into one entry."""
        asm = KeyedMultilineAssembler()
        stream_key = "docker:py_container_456"

        await asm.feed(stream_key, _make_entry(
            message="Traceback (most recent call last):",
            severity=3,
        ))
        await asm.feed(stream_key, _make_entry(
            message='  File "/app/main.py", line 42, in handle',
            severity=6,
        ))
        await asm.feed(stream_key, _make_entry(
            message="    return process(data)",
            severity=6,
        ))
        await asm.feed(stream_key, _make_entry(
            message="ValueError: invalid literal for int()",
            severity=3,
        ))

        # The ValueError line is NOT a continuation (no leading whitespace,
        # no 'at ', no 'Caused by:', etc.) — so it should flush the Traceback
        # buffer and start a new entry.
        await asyncio.sleep(0.3)

        q = get_queue()
        items = []
        while not q.empty():
            items.append(q.get_nowait())

        # Traceback lines should be merged, ValueError starts new entry
        assert len(items) == 2
        assert "Traceback" in items[0]["message"]
        assert "main.py" in items[0]["message"]
        assert items[1]["message"].startswith("ValueError")

    @pytest.mark.asyncio
    async def test_docker_separate_containers_isolated(self):
        """Logs from different containers should NOT be merged together."""
        asm = KeyedMultilineAssembler()

        await asm.feed("docker:container_a", _make_entry(
            message="Error from container A",
            app_name="nginx",
        ))
        await asm.feed("docker:container_b", _make_entry(
            message="Error from container B",
            app_name="redis",
        ))
        await asm.feed("docker:container_a", _make_entry(
            message="  detail line for A",
            app_name="nginx",
        ))

        await asyncio.sleep(0.3)

        q = get_queue()
        items = []
        while not q.empty():
            items.append(q.get_nowait())

        # container_b should have its own entry
        b_items = [i for i in items if i["app_name"] == "redis"]
        assert len(b_items) == 1
        assert b_items[0]["message"] == "Error from container B"

        # container_a should have merged entry
        a_items = [i for i in items if i["app_name"] == "nginx"]
        assert len(a_items) == 1
        assert "Error from container A" in a_items[0]["message"]
        assert "detail line for A" in a_items[0]["message"]

    @pytest.mark.asyncio
    async def test_docker_source_alias(self):
        """Docker log entries should have source_alias='unraid-docker'."""
        entry = _make_log_entry("my-nginx", "abc123", "Started server")
        assert entry["source_alias"] == "unraid-docker"
        assert entry["app_name"] == "my-nginx"
        assert entry["source_ip"] == "docker"


# ===================================================================
# 3. CLI password reset
# ===================================================================

class TestCLIPasswordReset:

    def test_reset_admin_on_empty_table(self, db_path: Path):
        """reset-admin should INSERT when admin_auth is empty."""
        reset_admin("newpassword123", str(db_path))

        conn = get_connection(db_path)
        row = conn.execute(
            "SELECT id, password_hash, created_at, updated_at FROM admin_auth"
        ).fetchone()
        conn.close()

        assert row is not None
        assert row[0] == 1  # id
        # Verify it's a valid Argon2id hash
        assert row[1].startswith("$argon2id$")

        # Verify the password actually verifies
        ph = PasswordHasher()
        assert ph.verify(row[1], "newpassword123")

    def test_reset_admin_on_existing_row(self, db_path: Path):
        """reset-admin should UPDATE when admin_auth already has a row."""
        # Insert initial password
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

        # Now reset
        reset_admin("newpassword456", str(db_path))

        conn = get_connection(db_path)
        row = conn.execute(
            "SELECT password_hash, created_at, updated_at FROM admin_auth WHERE id=1"
        ).fetchone()
        conn.close()

        assert row is not None
        new_hash = row[0]
        assert new_hash.startswith("$argon2id$")
        # Old password should NOT verify
        with pytest.raises(Exception):
            ph.verify(new_hash, "oldpassword")
        # New password should verify
        assert ph.verify(new_hash, "newpassword456")
        # created_at should be preserved (from the original INSERT)
        assert row[1] == "2024-01-01T00:00:00"
        # updated_at should be newer
        assert row[2] != "2024-01-01T00:00:00"

    def test_reset_admin_argon2id_algorithm(self, db_path: Path):
        """Password hash must use argon2id variant."""
        reset_admin("testpass", str(db_path))

        conn = get_connection(db_path)
        row = conn.execute(
            "SELECT password_hash FROM admin_auth WHERE id=1"
        ).fetchone()
        conn.close()

        assert row is not None
        hash_str = row[0]
        assert "$argon2id$" in hash_str

    def test_reset_admin_only_one_row(self, db_path: Path):
        """After multiple resets, there should still be exactly 1 row."""
        reset_admin("pass1", str(db_path))
        reset_admin("pass2", str(db_path))
        reset_admin("pass3", str(db_path))

        conn = get_connection(db_path)
        count = conn.execute("SELECT COUNT(*) FROM admin_auth").fetchone()[0]
        conn.close()

        assert count == 1


# ===================================================================
# 4. Docker host parsing
# ===================================================================

class TestDockerHostParsing:

    def test_unix_socket_default(self):
        """Default DOCKER_HOST should parse to unix socket."""
        with patch.dict(os.environ, {}, clear=False):
            # Remove DOCKER_HOST if present
            os.environ.pop("DOCKER_HOST", None)
            base_url, uds_path = _parse_docker_host()
            assert uds_path == "/var/run/docker.sock"
            assert "localhost" in base_url

    def test_unix_socket_explicit(self):
        """Explicit unix:// DOCKER_HOST should parse correctly."""
        with patch.dict(os.environ, {"DOCKER_HOST": "unix:///var/run/docker.sock"}):
            base_url, uds_path = _parse_docker_host()
            assert uds_path == "/var/run/docker.sock"
            assert "localhost" in base_url

    def test_tcp_proxy(self):
        """tcp:// DOCKER_HOST should parse to HTTP endpoint with no UDS."""
        with patch.dict(os.environ, {"DOCKER_HOST": "tcp://proxy:2375"}):
            base_url, uds_path = _parse_docker_host()
            assert uds_path is None
            assert "proxy" in base_url
            assert "2375" in base_url

    def test_tcp_custom_port(self):
        """tcp:// with custom port should be preserved."""
        with patch.dict(os.environ, {"DOCKER_HOST": "tcp://192.168.1.100:2376"}):
            base_url, uds_path = _parse_docker_host()
            assert uds_path is None
            assert "192.168.1.100" in base_url
            assert "2376" in base_url


# ===================================================================
# 5. Docker log line parsing
# ===================================================================

class TestDockerLogParsing:

    def test_multiplexed_stdout_frame(self):
        """Parse a Docker multiplexed stdout frame."""
        # stream_type=1 (stdout), padding=0,0,0, size=12 big-endian
        payload = b"Hello World!"
        header = bytes([1, 0, 0, 0]) + len(payload).to_bytes(4, "big")
        frame = header + payload
        result = _parse_docker_log_line(frame)
        assert result == "Hello World!"

    def test_multiplexed_stderr_frame(self):
        """Parse a Docker multiplexed stderr frame."""
        payload = b"Error occurred"
        header = bytes([2, 0, 0, 0]) + len(payload).to_bytes(4, "big")
        frame = header + payload
        result = _parse_docker_log_line(frame)
        assert result == "Error occurred"

    def test_raw_tty_mode_fallback(self):
        """Non-framed text should be returned as-is (TTY mode)."""
        raw = b"Just a plain log line\n"
        result = _parse_docker_log_line(raw)
        assert result == "Just a plain log line"

    def test_make_log_entry_fields(self):
        """_make_log_entry should produce correct dict shape."""
        entry = _make_log_entry("my-app", "abc123def", "Server started")
        assert entry["source_alias"] == "unraid-docker"
        assert entry["app_name"] == "my-app"
        assert entry["source_ip"] == "docker"
        assert entry["message"] == "Server started"
        assert entry["severity"] == 6
        assert entry["facility"] == 1
        # Should have valid ISO timestamps
        datetime.datetime.fromisoformat(entry["timestamp"])
        datetime.datetime.fromisoformat(entry["received_at"])
