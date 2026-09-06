"""
Tests for Docker collector, framing, container filtering, and socket fallback.
"""

import asyncio
import datetime
import logging
import os
from unittest.mock import patch
import pytest
import httpx

from app.core.pipeline import KeyedMultilineAssembler
from app.collectors.docker_collector import (
    DockerTailer,
    _detect_severity,
    _make_log_entry,
    _parse_docker_host,
    _parse_docker_log_line,
    _should_ignore_container,
    _tail_container_logs,
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
        tailer = DockerTailer(assembler, socket_poll_interval=0.01, socket_poll_max=0.02)

        task = asyncio.create_task(tailer.run())
        await asyncio.sleep(0.06)

        assert "Polling for socket before fallback" in caplog.text
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
    async def test_socket_polling_attaches_when_socket_appears(self, monkeypatch, caplog):
        """If docker.sock is missing at first but appears during polling, DockerTailer connects."""
        caplog.set_level(logging.INFO)
        monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")

        exists_calls = 0
        def _mock_exists(path):
            nonlocal exists_calls
            if path == "/var/run/docker.sock":
                exists_calls += 1
                return exists_calls >= 2  # becomes True on 2nd check
            return True

        monkeypatch.setattr(os.path, "exists", _mock_exists)

        assembler = KeyedMultilineAssembler()
        tailer = DockerTailer(assembler, socket_poll_interval=0.02, socket_poll_max=0.5)

        async def _mock_attach(client):
            pass

        async def _mock_watch(client):
            await tailer._cancel_event.wait()

        monkeypatch.setattr(tailer, "_attach_running_containers", _mock_attach)
        monkeypatch.setattr(tailer, "_watch_events", _mock_watch)

        task = asyncio.create_task(tailer.run())
        await asyncio.sleep(0.08)

        assert "Docker socket found at /var/run/docker.sock" in caplog.text
        await tailer.stop()
        await task

    @pytest.mark.asyncio
    async def test_docker_disabled_by_config(self, monkeypatch, caplog):
        """Setting DOCKER_HOST=none immediately operates in syslog-only mode without polling delay."""
        caplog.set_level(logging.INFO)
        monkeypatch.setenv("DOCKER_HOST", "none")

        assembler = KeyedMultilineAssembler()
        tailer = DockerTailer(assembler)

        task = asyncio.create_task(tailer.run())
        await asyncio.sleep(0.02)

        assert "Docker container tailing disabled by configuration; operating in syslog-only mode." in caplog.text
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


# ===================================================================
# 4. Tailer Task Map Cleanup & Reconnection
# ===================================================================

class TestDockerTailerTaskCleanup:

    @pytest.mark.asyncio
    async def test_tailer_reconnects_on_transient_disconnect(self):
        """Container tailer task auto-reconnects on transient disconnect/EOF and stays in _tailers."""
        assembler = KeyedMultilineAssembler()
        tailer = DockerTailer(assembler)

        container_id = "test_container_123456"
        container_name = "test_app"

        class MockResponse:
            def raise_for_status(self):
                pass

            async def aiter_bytes(self):
                if False:
                    yield b""

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        class MockClient:
            async def get(self, url, **kwargs):
                class InspectResp:
                    status_code = 200
                    def json(self):
                        return {"Config": {"Tty": False}}
                return InspectResp()

            def stream(self, method, url, **kwargs):
                return MockResponse()

        mock_client = MockClient()

        tailer._start_tailer(mock_client, container_id, container_name)
        assert container_id in tailer._tailers
        task, _ = tailer._tailers[container_id]

        # After EOF, the task does not die; it remains running and in _tailers
        await asyncio.sleep(0.05)
        assert not task.done()
        assert container_id in tailer._tailers

        # It terminates permanently when container is stopped
        await tailer._stop_tailer(container_id)
        assert task.done()
        assert container_id not in tailer._tailers

        await tailer.stop()

    @pytest.mark.asyncio
    async def test_tailer_task_permanent_termination_on_die_and_shutdown(self):
        """Tailers only terminate permanently when _stop_tailer() or stop() is called."""
        assembler = KeyedMultilineAssembler()
        tailer = DockerTailer(assembler)

        container_id = "test_container_die"
        container_name = "test_die_app"

        class MockResponse:
            def raise_for_status(self):
                pass

            async def aiter_bytes(self):
                await asyncio.sleep(10)
                if False:
                    yield b""

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        class MockClient:
            async def get(self, url, **kwargs):
                class InspectResp:
                    status_code = 200
                    def json(self):
                        return {"Config": {"Tty": False}}
                return InspectResp()

            def stream(self, method, url, **kwargs):
                return MockResponse()

        mock_client = MockClient()
        tailer._start_tailer(mock_client, container_id, container_name)
        assert container_id in tailer._tailers
        task, _ = tailer._tailers[container_id]
        assert not task.done()

        # Stop tailer (simulate container die event)
        await tailer._stop_tailer(container_id)
        assert task.done()
        assert container_id not in tailer._tailers

        await tailer.stop()


# ===================================================================
# 5. Log Stream Framing & Severity Mapping
# ===================================================================

class TestDockerLogStreamSeverityAndReconnect:

    @pytest.mark.asyncio
    async def test_multiplexed_stderr_and_stdout_severity(self):
        """Multiplexed stream frames parse severity from content, not falsely marking stderr as Error."""
        assembler = KeyedMultilineAssembler()
        entries = []

        async def capture_feed(key, entry):
            entries.append(entry)

        assembler.feed = capture_feed

        # Frame 1: stdout with info (stream_type = 1)
        stdout_msg = b"System initialized successfully\n"
        stdout_frame = bytes([1, 0, 0, 0]) + len(stdout_msg).to_bytes(4, "big") + stdout_msg

        # Frame 2: stderr with explicit ERROR (stream_type = 2)
        stderr_err_msg = b"ERROR: database connection failure\n"
        stderr_err_frame = bytes([2, 0, 0, 0]) + len(stderr_err_msg).to_bytes(4, "big") + stderr_err_msg

        # Frame 3: stderr with normal INFO (stream_type = 2)
        stderr_info_msg = b"[INFO] [celery.app.trace] Task succeeded in 0.05s\n"
        stderr_info_frame = bytes([2, 0, 0, 0]) + len(stderr_info_msg).to_bytes(4, "big") + stderr_info_msg

        combined_payload = stdout_frame + stderr_err_frame + stderr_info_frame

        cancel_event = asyncio.Event()

        class MockResponse:
            def raise_for_status(self):
                pass

            async def aiter_bytes(self):
                yield combined_payload
                cancel_event.set()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        class MockClient:
            async def get(self, url, **kwargs):
                class InspectResp:
                    status_code = 200
                    def json(self):
                        return {"Config": {"Tty": False}}
                return InspectResp()

            def stream(self, method, url, **kwargs):
                return MockResponse()

        mock_client = MockClient()
        await _tail_container_logs(
            mock_client,
            "cid123",
            "test_app",
            assembler,
            cancel_event,
        )

        assert len(entries) == 3
        # stdout without explicit level defaults to INFO
        assert entries[0]["message"] == "System initialized successfully"
        assert entries[0]["severity"] == 6  # RFC Info

        # stderr with ERROR gets Error
        assert entries[1]["message"] == "ERROR: database connection failure"
        assert entries[1]["severity"] == 3  # RFC Error

        # stderr with INFO stays INFO (does NOT become a false error)
        assert entries[2]["message"] == "[INFO] [celery.app.trace] Task succeeded in 0.05s"
        assert entries[2]["severity"] == 6  # RFC Info

    @pytest.mark.asyncio
    async def test_auto_reconnect_on_remote_protocol_error(self, monkeypatch):
        """Transient RemoteProtocolError triggers retry without terminating the stream task."""
        monkeypatch.setattr("app.collectors.docker_collector._CONTAINER_INITIAL_BACKOFF", 0.01)

        assembler = KeyedMultilineAssembler()
        entries = []

        async def capture_feed(key, entry):
            entries.append(entry)

        assembler.feed = capture_feed

        cancel_event = asyncio.Event()
        attempts = 0

        class MockResponse1:
            def raise_for_status(self):
                pass

            async def aiter_bytes(self):
                if False:
                    yield b""
                raise httpx.RemoteProtocolError("Connection reset by peer")

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        class MockResponse2:
            def raise_for_status(self):
                pass

            async def aiter_bytes(self):
                msg = b"Reconnected and alive\n"
                frame = bytes([1, 0, 0, 0]) + len(msg).to_bytes(4, "big") + msg
                yield frame
                cancel_event.set()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        class MockClient:
            async def get(self, url, **kwargs):
                class InspectResp:
                    status_code = 200
                    def json(self):
                        return {"Config": {"Tty": False}}
                return InspectResp()

            def stream(self, method, url, **kwargs):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    return MockResponse1()
                return MockResponse2()

        mock_client = MockClient()
        await _tail_container_logs(
            mock_client,
            "cid123",
            "test_app",
            assembler,
            cancel_event,
        )

        assert attempts == 2
        assert len(entries) == 1
        assert entries[0]["message"] == "Reconnected and alive"
        assert entries[0]["severity"] == 6


# ===================================================================
# 6. Content-Aware Severity Detection
# ===================================================================

class TestDetectSeverity:

    def test_logfmt_severity(self):
        assert _detect_severity('time="2026-09-06T10:28:14Z" level=info msg="cache refreshed"') == 6
        assert _detect_severity('time="2026-09-06T10:28:14Z" level=error msg="failed to connect"') == 3
        assert _detect_severity('time="2026-09-06T10:28:14Z" level=warn msg="high memory"') == 4
        assert _detect_severity('level=warning msg="deprecated API"') == 4
        assert _detect_severity('lvl=debug msg="trace context"') == 7

    def test_bracketed_severity(self):
        assert _detect_severity("[2026-09-06 11:27:00,104] [INFO] [celery.app.trace] Task succeeded") == 6
        assert _detect_severity("[ERROR] database pool exhausted") == 3
        assert _detect_severity("[WARN] slow disk response") == 4
        assert _detect_severity("[DEBUG] user authenticated") == 7
        assert _detect_severity("[FATAL] out of memory") == 2

    def test_colon_prefix_severity(self):
        assert _detect_severity("INFO: 127.0.0.1:40676 - 'GET /api/healthz HTTP/1.1' 200 OK") == 6
        assert _detect_severity("ERROR: 127.0.0.1:40676 - 'GET /api/data HTTP/1.1' 500 Internal Server Error") == 3
        assert _detect_severity("WARNING: configuration file missing key, using default") == 4
        assert _detect_severity("LOG: checkpoint complete: wrote 25 buffers") == 6

    def test_timestamp_delimited_severity(self):
        assert _detect_severity("2026-09-06 10:20:04 INFO All scopes processed") == 6
        assert _detect_severity("2026-09-06 10:20:04 ERROR Sync failed for target host") == 3
        assert _detect_severity("2026-09-06 10:20:04 WARN Retry attempt 1") == 4

    def test_ansi_colored_severity(self):
        assert _detect_severity("2026-09-06T10:27:00.012Z \x1b[32minfo\x1b[39m|Jobs|: Starting scheduled job") == 6
        assert _detect_severity("2026-09-06T10:27:00.012Z \x1b[31merror\x1b[39m|Jobs|: Job failed") == 3

    def test_json_severity(self):
        assert _detect_severity('{"level": "info", "message": "server listening"}') == 6
        assert _detect_severity('{"level": "error", "message": "unhandled rejection"}') == 3

    def test_exceptions_and_panics(self):
        assert _detect_severity("Traceback (most recent call last):") == 3
        assert _detect_severity("Exception: failed to parse config") == 3
        assert _detect_severity("panic: runtime error: invalid memory address") == 2

    def test_fallback_unstructured_text(self):
        # Normal lines with no explicit log level default to RFC Info (6)
        assert _detect_severity("Server configuration was transferred successfully.") == 6
        assert _detect_severity("DHCP Server leased IP 172.22.2.161 to Wii") == 6
        assert _detect_severity("") == 6
