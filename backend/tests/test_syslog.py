"""
Tests for RFC 3164 and RFC 5424 parsing, timestamp handling, network listeners, and alias resolution.
"""

import asyncio
import datetime
from pathlib import Path
import pytest

from app.core.migrations import get_connection, run_migrations
from app.core.pipeline import KeyedMultilineAssembler
from app.collectors.syslog import (
    AliasCache,
    SyslogServer,
    SyslogTCPProtocol,
    SyslogUDPProtocol,
    parse_syslog_message,
    resolve_alias,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "syslog_test.db"
    run_migrations(p)
    return p


# ===================================================================
# 1. RFC 3164 / RFC 5424 Syslog Parsing
# ===================================================================

class TestSyslogParsing:

    def test_rfc3164_basic(self):
        raw = b"<14>Jan  5 10:30:00 myhost myapp[123]: Something happened"
        result = parse_syslog_message(raw, "192.168.1.1")

        assert result["facility"] == 1      # 14 // 8
        assert result["severity"] == 6      # 14 % 8
        assert result["app_name"] == "myapp"
        assert result["source_ip"] == "192.168.1.1"
        assert "Something happened" in result["message"]

    def test_rfc3164_no_pid(self):
        raw = b"<38>Feb 12 08:15:30 router sshd: Bad password attempt"
        result = parse_syslog_message(raw, "10.0.0.1")

        assert result["facility"] == 4      # 38 // 8
        assert result["severity"] == 6      # 38 % 8
        assert result["app_name"] == "sshd"
        assert "Bad password" in result["message"]

    def test_rfc5424_basic(self):
        raw = b"<134>1 2024-01-15T10:30:00.000Z myhost myapp 1234 ID47 - Application started"
        result = parse_syslog_message(raw, "10.0.0.1")

        assert result["facility"] == 16     # 134 // 8
        assert result["severity"] == 6      # 134 % 8
        assert result["app_name"] == "myapp"
        assert result["timestamp"] == "2024-01-15T10:30:00.000Z"
        assert "Application started" in result["message"]

    def test_rfc5424_nil_fields(self):
        raw = b"<165>1 2024-03-01T12:00:00Z - - - - - Just a message"
        result = parse_syslog_message(raw, "10.1.1.1")

        assert result["severity"] == 5      # 165 % 8
        assert result["app_name"] == "unknown"  # '-' maps to unknown
        assert "Just a message" in result["message"]

    def test_rfc5424_structured_data_with_spaces(self):
        raw = b'<134>1 2024-01-15T10:30:00.000Z srv01 myapp 1234 ID47 [meta key="value with spaces" tag="audit"] Actual message text'
        result = parse_syslog_message(raw, "10.0.0.1")

        assert result["facility"] == 16
        assert result["severity"] == 6
        assert result["app_name"] == "myapp"
        assert result.get("hostname") == "srv01"
        assert result["timestamp"] == "2024-01-15T10:30:00.000Z"
        assert result["message"] == "Actual message text"

    def test_rfc5424_multiple_structured_data_elements(self):
        raw = b'<134>1 2024-01-15T10:30:00.000Z srv01 myapp 1234 ID47 [sd1 a="1"][sd2 b="2"] Multiblock message'
        result = parse_syslog_message(raw, "10.0.0.1")

        assert result["app_name"] == "myapp"
        assert result["message"] == "Multiblock message"

    def test_rfc3164_message_starting_with_number_not_misidentified_as_5424(self):
        raw = b"<134>Jan 15 10:30:00 srv01 myapp: 42 connections opened"
        result = parse_syslog_message(raw, "10.0.0.1")

        assert result["facility"] == 16
        assert result["severity"] == 6
        assert result["app_name"] == "myapp"
        assert result["message"] == "42 connections opened"

    def test_unparseable_fallback(self):
        raw = b"This is not a syslog message at all"
        result = parse_syslog_message(raw, "10.0.0.1")

        assert result["severity"] == 6
        assert result["facility"] == 1
        assert result["app_name"] == "unknown"
        assert result["raw"] == "This is not a syslog message at all"

    def test_source_ip_passthrough(self):
        raw = b"<14>Jan  1 00:00:00 h app: msg"
        result = parse_syslog_message(raw, "172.16.0.99")
        assert result["source_ip"] == "172.16.0.99"

    def test_received_at_populated(self):
        raw = b"<14>Jan  1 00:00:00 h app: msg"
        result = parse_syslog_message(raw, "10.0.0.1")
        assert result["received_at"] is not None
        datetime.datetime.fromisoformat(result["received_at"])

    def test_raw_preserved(self):
        raw = b"<14>Jan  1 00:00:00 h app: msg"
        result = parse_syslog_message(raw, "10.0.0.1")
        assert result["raw"] == raw.decode("utf-8").strip()

    def test_utf8_with_errors(self):
        raw = b"<14>Jan  1 00:00:00 h app: hello \xff world"
        result = parse_syslog_message(raw, "10.0.0.1")
        assert "\ufffd" in result["raw"] or "world" in result["raw"]

    def test_rfc3164_local_timezone_ahead_of_utc(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        future_hour = now + datetime.timedelta(hours=1)
        month_str = future_hour.strftime("%b")
        day_str = f"{future_hour.day:2d}"
        time_str = future_hour.strftime("%H:%M:%S")
        raw = f"<14>{month_str} {day_str} {time_str} myhost myapp: test timezone".encode()
        result = parse_syslog_message(raw, "10.0.0.1")
        parsed_dt = datetime.datetime.fromisoformat(result["timestamp"])
        assert (parsed_dt - now).total_seconds() <= 60


# ===================================================================
# 2. Network Listeners & Protocols
# ===================================================================

class TestSyslogNetworkAndProtocol:

    @pytest.mark.asyncio
    async def test_tcp_syslog_buffer_limit_disconnects(self, db_path: Path):
        """TCP protocol should discard buffer and close connection if buffer exceeds 64KB without newline."""
        asm = KeyedMultilineAssembler()
        alias_cache = AliasCache(db_path)
        proto = SyslogTCPProtocol(asm, alias_cache)

        class MockTransport:
            def __init__(self):
                self.closed = False
            def get_extra_info(self, name):
                return ("192.168.1.100", 514)
            def close(self):
                self.closed = True

        transport = MockTransport()
        proto.connection_made(transport)

        oversized_chunk = b"A" * (70 * 1024)
        proto.data_received(oversized_chunk)

        assert transport.closed is True
        assert proto.buffer == b""

    @pytest.mark.asyncio
    async def test_tcp_syslog_newline_framing(self, db_path: Path):
        """TCP protocol splits incoming chunks on newline and feeds assembler."""
        received = []

        class MockAssembler:
            async def feed(self, stream_key: str, entry: dict):
                received.append((stream_key, entry))

        alias_cache = AliasCache(db_path)
        proto = SyslogTCPProtocol(MockAssembler(), alias_cache)

        class MockTransport:
            def get_extra_info(self, name):
                return ("10.0.0.5", 54321)
            def close(self):
                pass

        proto.connection_made(MockTransport())

        data = b"<14>Jan  1 10:00:00 host1 app1: line 1\n<14>Jan  1 10:00:01 host1 app1: line 2\n"
        proto.data_received(data)
        await asyncio.sleep(0.05)

        assert len(received) == 2
        assert received[0][1]["message"] == "line 1"
        assert received[1][1]["message"] == "line 2"

    @pytest.mark.asyncio
    async def test_udp_syslog_packet_handling(self, db_path: Path):
        """UDP protocol receives datagram and feeds assembler with resolved alias."""
        received = []

        class MockAssembler:
            async def feed(self, stream_key: str, entry: dict):
                received.append((stream_key, entry))

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with get_connection(db_path) as conn:
            conn.execute(
                "INSERT INTO host_aliases (ip, alias, created_at) VALUES ('10.0.0.9', 'nas-box', ?)",
                (now,),
            )
            conn.commit()

        alias_cache = AliasCache(db_path)
        alias_cache.load_aliases()

        proto = SyslogUDPProtocol(MockAssembler(), alias_cache)
        proto.datagram_received(
            b"<14>Jan  1 10:00:00 host1 app1: hello udp",
            ("10.0.0.9", 514),
        )
        await asyncio.sleep(0.05)

        assert len(received) == 1
        assert received[0][0] == "10.0.0.9:app1"
        assert received[0][1]["source_alias"] == "nas-box"
        assert received[0][1]["message"] == "hello udp"

    @pytest.mark.asyncio
    async def test_syslog_server_start_and_stop(self, db_path: Path):
        """SyslogServer manages UDP and TCP sockets and shuts down cleanly."""
        asm = KeyedMultilineAssembler()
        server = SyslogServer(asm, db_path, host="127.0.0.1", port=11514)

        await server.start()
        assert server.udp_transport is not None
        assert server.tcp_server is not None

        await server.stop()
        assert server.alias_cache._refresh_task is None


# ===================================================================
# 3. Host Alias Cache & Resolution
# ===================================================================

class TestAliasCache:

    def test_alias_cache_resolves_known_ip(self, db_path: Path):
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with get_connection(db_path) as conn:
            conn.execute(
                "INSERT INTO host_aliases (ip, alias, created_at) VALUES ('192.168.1.50', 'pihole', ?)",
                (now,),
            )
            conn.commit()

        cache = AliasCache(db_path)
        cache.load_aliases()
        assert cache.resolve("192.168.1.50") == "pihole"

    def test_alias_cache_falls_back_to_ip(self, db_path: Path):
        cache = AliasCache(db_path)
        cache.load_aliases()
        assert cache.resolve("10.99.99.99") == "10.99.99.99"

    def test_alias_cache_bulk_load(self, db_path: Path):
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with get_connection(db_path) as conn:
            for i in range(50):
                conn.execute(
                    "INSERT INTO host_aliases (ip, alias, created_at) VALUES (?, ?, ?)",
                    (f"10.0.0.{i}", f"host-{i}", now),
                )
            conn.commit()

        cache = AliasCache(db_path)
        cache.load_aliases()

        for i in range(50):
            assert cache.resolve(f"10.0.0.{i}") == f"host-{i}"
        assert cache.resolve("10.0.0.200") == "10.0.0.200"

    def test_alias_resolution_persisted(self, db_path: Path):
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with get_connection(db_path) as conn:
            conn.execute(
                "INSERT INTO host_aliases (ip, alias, created_at) VALUES ('192.168.1.99', 'truenas-core', ?)",
                (now,),
            )
            conn.commit()

        resolved = resolve_alias("192.168.1.99", db_path)
        assert resolved == "truenas-core"

    @pytest.mark.asyncio
    async def test_alias_cache_async_lifecycle_and_refresh(self, db_path: Path):
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        cache = AliasCache(db_path, refresh_interval=0.05)
        await cache.start()

        assert cache.resolve("192.168.1.10") == "192.168.1.10"

        with get_connection(db_path) as conn:
            conn.execute(
                "INSERT INTO host_aliases (ip, alias, created_at) VALUES ('192.168.1.10', 'nas-primary', ?)",
                (now,),
            )
            conn.commit()

        await asyncio.sleep(0.12)
        assert cache.resolve("192.168.1.10") == "nas-primary"

        await cache.stop()
        assert cache._refresh_task is None
