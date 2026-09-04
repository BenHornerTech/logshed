"""
Tests for Docker collector, framing, container filtering, and socket fallback.
"""

import asyncio
import datetime
import logging
import os
from unittest.mock import patch
import pytest

from app.core.pipeline import KeyedMultilineAssembler
from app.collectors.docker_collector import (
    DockerTailer,
    _make_log_entry,
    _parse_docker_host,
    _parse_docker_log_line,
    _should_ignore_container,
)


# ===================================================================
# 1. Docker Host Parsing
# ===================================================================

class TestDockerHostParsing:

    def test_unix_socket_default(self):
        """Default DOCKER_HOST should parse to unix socket."""
        with patch.dict(os.environ, {}, clear=False):
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

    def test_http_scheme(self):
        """http:// DOCKER_HOST should parse correctly."""
        with patch.dict(os.environ, {"DOCKER_HOST": "http://192.168.1.50:2375"}):
            base_url, uds_path = _parse_docker_host()
            assert uds_path is None
            assert base_url == "http://192.168.1.50:2375/v1.43"

    def test_https_scheme(self):
        """https:// DOCKER_HOST should parse correctly."""
        with patch.dict(os.environ, {"DOCKER_HOST": "https://docker.lan:2376"}):
            base_url, uds_path = _parse_docker_host()
            assert uds_path is None
            assert base_url == "https://docker.lan:2376/v1.43"


# ===================================================================
# 2. Docker Log Framing & Parsing
# ===================================================================

class TestDockerLogParsing:

    def test_multiplexed_stdout_frame(self):
        """Parse a Docker multiplexed stdout frame (stream_type=1)."""
        payload = b"Hello World!"
        header = bytes([1, 0, 0, 0]) + len(payload).to_bytes(4, "big")
        frame = header + payload
        result = _parse_docker_log_line(frame)
        assert result == "Hello World!"

    def test_multiplexed_stderr_frame(self):
        """Parse a Docker multiplexed stderr frame (stream_type=2)."""
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
        """_make_log_entry should produce correct dict shape defaulting to 'docker'."""
        entry = _make_log_entry("my-app", "abc123def", "Server started")
        assert entry["source_alias"] == "docker"
        assert entry["app_name"] == "my-app"
        assert entry["source_ip"] == "docker"
        assert entry["message"] == "Server started"
        assert entry["severity"] == 6
        assert entry["facility"] == 1
        datetime.datetime.fromisoformat(entry["timestamp"])
        datetime.datetime.fromisoformat(entry["received_at"])

    def test_make_log_entry_source_alias_override(self, monkeypatch):
        """_make_log_entry should respect DOCKER_SOURCE_ALIAS environment variable."""
        monkeypatch.setenv("DOCKER_SOURCE_ALIAS", "pve-node1-docker")
        entry = _make_log_entry("redis-cache", "xyz789", "Ready to accept connections")
        assert entry["source_alias"] == "pve-node1-docker"
        assert entry["app_name"] == "redis-cache"

    def test_should_ignore_container(self, monkeypatch):
        """Self-containers and explicitly excluded containers should be ignored."""
        # Default self-container names
        assert _should_ignore_container("abc123456789", "logshed") is True
        assert _should_ignore_container("abc123456789", "/log_shed") is True
        assert _should_ignore_container("abc123456789", "log-shed") is True

        # Non-self container
        assert _should_ignore_container("def987654321", "nginx") is False
        assert _should_ignore_container("def987654321", "/nextcloud") is False

        # Match container short ID against HOSTNAME env var
        monkeypatch.setenv("HOSTNAME", "abc123456789")
        assert _should_ignore_container("abc123456789def012345678", "custom-app-name") is True

        # Match custom DOCKER_EXCLUDE_CONTAINERS
        monkeypatch.setenv("DOCKER_EXCLUDE_CONTAINERS", "my-db,custom_redis")
        assert _should_ignore_container("111222333444", "my-db") is True
        assert _should_ignore_container("111222333444", "/custom_redis") is True
        assert _should_ignore_container("111222333444", "plex") is False


# ===================================================================
# 3. Graceful Docker Socket Fallback
# ===================================================================

class TestDockerSocketFallback:

    @pytest.mark.asyncio
    async def test_socket_not_found_fallback(self, monkeypatch, caplog):
        caplog.set_level(logging.INFO)
        monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")

        orig_exists = os.path.exists

        def _mock_exists(path):
            if path == "/var/run/docker.sock":
                return False
            return orig_exists(path)

        monkeypatch.setattr(os.path, "exists", _mock_exists)

        assembler = KeyedMultilineAssembler()
        tailer = DockerTailer(assembler)

        task = asyncio.create_task(tailer.run())
        await asyncio.sleep(0.05)

        expected_msg = (
            "Docker socket not found at /var/run/docker.sock. "
            "Docker container tailing disabled; operating in syslog-only mode."
        )
        assert expected_msg in caplog.text
        assert not task.done()

        await tailer.stop()
        await task
        assert task.done()

    @pytest.mark.asyncio
    async def test_remote_tcp_connection_failure_logging(self, monkeypatch, caplog):
        """Remote TCP connection failures should log descriptive error with target URL."""
        caplog.set_level(logging.WARNING)
        # Point to an unallocated port on localhost to trigger connection failure
        monkeypatch.setenv("DOCKER_HOST", "tcp://127.0.0.1:59999")

        assembler = KeyedMultilineAssembler()
        tailer = DockerTailer(assembler)

        task = asyncio.create_task(tailer.run())
        await asyncio.sleep(0.15)

        await tailer.stop()
        await task
        assert "Docker connection error at http://127.0.0.1:59999" in caplog.text
