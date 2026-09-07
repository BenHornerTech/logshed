"""
Tests for RFC 3164 and RFC 5424 parsing, timestamp handling, network listeners, and alias resolution.
"""

import asyncio
import datetime
from pathlib import Path
import pytest

from app.core.config import get_syslog_port
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
        assert result["timestamp"] == "2024-01-15T10:30:00+00:00"
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
        assert result["timestamp"] == "2024-01-15T10:30:00+00:00"
        assert result["message"] == "Actual message text"

    def test_rfc5424_multiple_structured_data_elements(self):
        raw = b'<134>1 2024-01-15T10:30:00.000Z srv01 myapp 1234 ID47 [sd1 a="1"][sd2 b="2"] Multiblock message'
        result = parse_syslog_message(raw, "10.0.0.1")

        assert result["app_name"] == "myapp"
        assert result["message"] == "Multiblock message"

    def test_rfc5424_multiple_structured_data_elements_with_spaces(self):
        """RFC 5424 SD elements separated by spaces should all be parsed without polluting message text."""
        raw = b'<134>1 2024-01-15T10:30:00.000Z srv01 myapp 1234 ID47 [sd1 a="1"] [sd2 b="2"] [sd3 c="3"] Clean message text'
        result = parse_syslog_message(raw, "10.0.0.1")

        assert result["app_name"] == "myapp"
        assert result["hostname"] == "srv01"
        assert result["message"] == "Clean message text"

    def test_rfc3164_sets_hostname(self):
        """RFC 3164 messages should extract and set result['hostname']."""
        raw = b"<14>Jan  5 10:30:00 webserver01 nginx[123]: Request processed"
        result = parse_syslog_message(raw, "192.168.1.100")

        assert result["hostname"] == "webserver01"
        assert result["app_name"] == "nginx"
        assert result["message"] == "Request processed"

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

    def test_rfc5424_future_zulu_timestamp_clamped_to_arrival_time(self):
        """RFC 5424 timestamp ending in 'Z' that is far in the future must be clamped to arrival time."""
        now = datetime.datetime.now(datetime.timezone.utc)
        future_time = now + datetime.timedelta(hours=5)
        future_iso = future_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        raw = f"<134>1 {future_iso} myhost myapp 1234 ID47 - Future time message".encode()
        result = parse_syslog_message(raw, "10.0.0.1")
        parsed_dt = datetime.datetime.fromisoformat(result["timestamp"])
        diff = abs((parsed_dt - now).total_seconds())
        assert diff <= 60
        assert result["timestamp"] != future_iso

    def test_rfc3164_omitted_hostname_with_pid(self):
        """RFC 3164 messages where hostname is omitted and MSG (TAG[PID]:) directly follows timestamp."""
        raw = b"<78>Sep  7 14:15:00 crond[330276]: USER root pid 718623 cmd run-parts /etc/periodic/15min"
        result = parse_syslog_message(raw, "192.168.1.50")

        assert result["facility"] == 9       # 78 // 8 (cron)
        assert result["severity"] == 6       # 78 % 8 (info)
        assert result["app_name"] == "crond"
        assert result["message"] == "USER root pid 718623 cmd run-parts /etc/periodic/15min"
        assert "hostname" not in result
        assert result["source_ip"] == "192.168.1.50"

    def test_rfc3164_omitted_hostname_without_pid(self):
        """RFC 3164 message where hostname is omitted and MSG (TAG:) directly follows timestamp."""
        raw = b"<14>Jan  5 10:30:00 myapp: Something happened"
        result = parse_syslog_message(raw, "10.0.0.1")

        assert result["facility"] == 1
        assert result["severity"] == 6
        assert result["app_name"] == "myapp"
        assert result["message"] == "Something happened"
        assert "hostname" not in result

    def test_rfc3164_omitted_hostname_no_colon_with_pid(self):
        """RFC 3164 message where hostname is omitted and MSG has PID but no colon."""
        raw = b"<14>Jan  5 10:30:00 myapp[123] Something happened"
        result = parse_syslog_message(raw, "10.0.0.1")

        assert result["facility"] == 1
        assert result["severity"] == 6
        assert result["app_name"] == "myapp"
        assert result["message"] == "Something happened"
        assert "hostname" not in result

    def test_rfc3164_bracketed_tag_without_hostname(self):
        """RFC 3164 message with bracketed process name (e.g. [kernel]) must not discard the bracketed token."""
        raw = b"<14>Sep  7 14:15:00 [kernel] USB disconnected"
        result = parse_syslog_message(raw, "10.0.0.1")

        assert result["app_name"] == "kernel"
        assert result["message"] == "USB disconnected"
        assert "hostname" not in result

    def test_rfc3164_bracketed_tag_with_colon(self):
        """RFC 3164 message with bracketed process name and colon (e.g. [kernel]:)."""
        raw = b"<14>Sep  7 14:15:00 [kernel]: USB disconnected"
        result = parse_syslog_message(raw, "10.0.0.1")

        assert result["app_name"] == "kernel"
        assert result["message"] == "USB disconnected"
        assert "hostname" not in result

    def test_rfc3164_hostname_with_pid_no_colon(self):
        """RFC 3164 message with hostname and process PID without colon must cleanly strip PID from app_name."""
        raw = b"<14>Sep  7 14:15:00 myhost crond[330276] USER root pid 718623"
        result = parse_syslog_message(raw, "10.0.0.1")

        assert result["hostname"] == "myhost"
        assert result["app_name"] == "crond"
        assert result["message"] == "USER root pid 718623"

    def test_rfc3164_nil_hostname(self):
        """RFC 3164 message with '-' as NIL hostname should not treat '-' as hostname."""
        raw = b"<14>Sep  7 14:15:00 - myapp: message content"
        result = parse_syslog_message(raw, "10.0.0.1")

        assert "hostname" not in result
        assert result["app_name"] == "myapp"
        assert result["message"] == "message content"

    def test_rfc3164_single_word_message(self):
        """RFC 3164 message with single word after timestamp."""
        raw = b"<14>Sep  7 14:15:00 reboot"
        result = parse_syslog_message(raw, "10.0.0.1")

        assert "hostname" not in result
        assert result["message"] == "reboot"

    def test_rfc3164_ipv6_hostname(self):
        """RFC 3164 message with IPv6 hostname."""
        raw = b"<14>Sep  7 14:15:00 2001:db8::1 crond[123]: test message"
        result = parse_syslog_message(raw, "10.0.0.1")

        assert result["hostname"] == "2001:db8::1"
        assert result["app_name"] == "crond"
        assert result["message"] == "test message"


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
    async def test_udp_syslog_unaliased_ip_adopts_parsed_hostname(self, db_path: Path):
        """UDP message from unaliased IP should use parsed hostname as source_alias."""
        received = []

        class MockAssembler:
            async def feed(self, stream_key: str, entry: dict):
                received.append((stream_key, entry))

        alias_cache = AliasCache(db_path)
        alias_cache.load_aliases()

        proto = SyslogUDPProtocol(MockAssembler(), alias_cache)

        # RFC 3164 with hostname 'gateway01'
        proto.datagram_received(
            b"<14>Jan  1 10:00:00 gateway01 dhcpd: DHCPACK on 192.168.1.50",
            ("192.168.1.1", 514),
        )
        # RFC 5424 with hostname 'pve-node2'
        proto.datagram_received(
            b"<134>1 2024-01-15T10:30:00.000Z pve-node2 qemu-server 1234 ID47 - VM 100 started",
            ("192.168.1.2", 514),
        )
        await asyncio.sleep(0.05)

        assert len(received) == 2
        assert received[0][1]["source_alias"] == "gateway01"
        assert received[0][0] == "192.168.1.1:dhcpd"
        assert received[1][1]["source_alias"] == "pve-node2"
        assert received[1][0] == "192.168.1.2:qemu-server"

    @pytest.mark.asyncio
    async def test_tcp_syslog_unaliased_ip_adopts_parsed_hostname(self, db_path: Path):
        """TCP message from unaliased IP should use parsed hostname as source_alias."""
        received = []

        class MockAssembler:
            async def feed(self, stream_key: str, entry: dict):
                received.append((stream_key, entry))

        alias_cache = AliasCache(db_path)
        alias_cache.load_aliases()

        proto = SyslogTCPProtocol(MockAssembler(), alias_cache)

        class MockTransport:
            def get_extra_info(self, name):
                return ("192.168.1.3", 54321)
            def close(self):
                pass

        proto.connection_made(MockTransport())

        proto.data_received(b"<14>Jan  1 10:00:00 pihole-dns dnsmasq: query[A] example.com\n")
        await asyncio.sleep(0.05)

        assert len(received) == 1
        assert received[0][1]["source_alias"] == "pihole-dns"
        assert received[0][1]["source_ip"] == "192.168.1.3"

    @pytest.mark.asyncio
    async def test_syslog_unaliased_ip_invalid_or_nil_hostname_retains_ip(self, db_path: Path):
        """When hostname is nil ('-') or unknown, source_alias should remain source_ip."""
        received = []

        class MockAssembler:
            async def feed(self, stream_key: str, entry: dict):
                received.append((stream_key, entry))

        alias_cache = AliasCache(db_path)
        alias_cache.load_aliases()

        proto = SyslogUDPProtocol(MockAssembler(), alias_cache)

        proto.datagram_received(
            b"<165>1 2024-03-01T12:00:00Z - app 1234 - - Message with nil hostname",
            ("192.168.1.4", 514),
        )
        await asyncio.sleep(0.05)

        assert len(received) == 1
        assert received[0][1]["source_alias"] == "192.168.1.4"

    @pytest.mark.asyncio
    async def test_syslog_omitted_hostname_uses_configured_alias_or_ip(self, db_path: Path):
        """Messages omitting hostname (e.g. crond[330276]:) should resolve via host_aliases or retain source_ip."""
        received = []

        class MockAssembler:
            async def feed(self, stream_key: str, entry: dict):
                received.append((stream_key, entry))

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with get_connection(db_path) as conn:
            conn.execute(
                "INSERT INTO host_aliases (ip, alias, created_at) VALUES ('192.168.1.75', 'npm-server', ?)",
                (now,),
            )
            conn.commit()

        alias_cache = AliasCache(db_path)
        alias_cache.load_aliases()

        proto = SyslogUDPProtocol(MockAssembler(), alias_cache)

        # 1. From IP with alias configured: adopts 'npm-server'
        proto.datagram_received(
            b"<78>Sep  7 14:15:00 crond[330276]: USER root pid 718623 cmd run-parts /etc/periodic/15min",
            ("192.168.1.75", 514),
        )
        # 2. From unaliased IP: retains source_ip ('192.168.1.76'), does NOT become 'crond[330276]'
        proto.datagram_received(
            b"<78>Sep  7 14:15:00 crond[330276]: USER root pid 718623 cmd run-parts /etc/periodic/15min",
            ("192.168.1.76", 514),
        )
        await asyncio.sleep(0.05)

        assert len(received) == 2
        # Message 1:
        assert received[0][1]["source_alias"] == "npm-server"
        assert received[0][1]["app_name"] == "crond"
        assert received[0][1]["message"] == "USER root pid 718623 cmd run-parts /etc/periodic/15min"
        assert "hostname" not in received[0][1]

        # Message 2:
        assert received[1][1]["source_alias"] == "192.168.1.76"
        assert received[1][1]["app_name"] == "crond"
        assert received[1][1]["message"] == "USER root pid 718623 cmd run-parts /etc/periodic/15min"
        assert "hostname" not in received[1][1]

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

    @pytest.mark.asyncio
    async def test_udp_syslog_bounded_queue_saturation_drops(self, db_path: Path):
        """When UDP protocol queue fills up, extra packets are dropped and counted."""
        alias_cache = AliasCache(db_path)
        asm = KeyedMultilineAssembler()
        # Initialize with max_queue_size=2 and num_workers=0 so queue fills
        proto = SyslogUDPProtocol(asm, alias_cache, max_queue_size=2, num_workers=0)
        from app.core.pipeline import get_dropped_count
        initial_drops = get_dropped_count()

        proto.datagram_received(b"<14>Jan  1 10:00:00 host1 app1: msg 1", ("10.0.0.1", 514))
        proto.datagram_received(b"<14>Jan  1 10:00:00 host1 app1: msg 2", ("10.0.0.1", 514))
        assert proto.queue.qsize() == 2

        # 3rd packet exceeds maxsize=2 and is dropped
        proto.datagram_received(b"<14>Jan  1 10:00:00 host1 app1: msg 3", ("10.0.0.1", 514))
        assert get_dropped_count() == initial_drops + 1
        await proto.stop()

    @pytest.mark.asyncio
    async def test_tcp_syslog_bounded_queue_saturation_drops(self, db_path: Path):
        """When TCP protocol queue fills up, extra lines are dropped and counted."""
        alias_cache = AliasCache(db_path)
        asm = KeyedMultilineAssembler()
        proto = SyslogTCPProtocol(asm, alias_cache, max_queue_size=2, num_workers=0)

        class MockTransport:
            def get_extra_info(self, name):
                return ("10.0.0.2", 54321)
            def close(self):
                pass

        proto.connection_made(MockTransport())
        from app.core.pipeline import get_dropped_count
        initial_drops = get_dropped_count()

        proto.data_received(b"<14>Jan  1 10:00:00 host1 app1: msg 1\n<14>Jan  1 10:00:00 host1 app1: msg 2\n")
        assert proto.queue.qsize() == 2

        proto.data_received(b"<14>Jan  1 10:00:00 host1 app1: msg 3\n")
        assert get_dropped_count() == initial_drops + 1
        await proto.stop()

    @pytest.mark.asyncio
    async def test_tcp_syslog_octet_counted_single_message(self, db_path: Path):
        """TCP protocol parses single RFC 6587 octet-counted frame."""
        received = []

        class MockAssembler:
            async def feed(self, stream_key: str, entry: dict):
                received.append((stream_key, entry))

        alias_cache = AliasCache(db_path)
        proto = SyslogTCPProtocol(MockAssembler(), alias_cache)

        class MockTransport:
            def get_extra_info(self, name):
                return ("10.0.0.8", 54321)
            def close(self):
                pass

        proto.connection_made(MockTransport())

        msg = b"<134>1 2024-01-15T10:30:00.000Z srv01 myapp 1234 ID47 - Octet-counted payload"
        frame = f"{len(msg)} ".encode("ascii") + msg

        proto.data_received(frame)
        await asyncio.sleep(0.05)

        assert len(received) == 1
        assert received[0][0] == "10.0.0.8:myapp"
        assert received[0][1]["message"] == "Octet-counted payload"
        assert received[0][1]["hostname"] == "srv01"
        await proto.stop()

    @pytest.mark.asyncio
    async def test_tcp_syslog_octet_counted_batched_messages(self, db_path: Path):
        """TCP protocol parses multiple RFC 6587 octet-counted frames in a single chunk (with and without newlines)."""
        received = []

        class MockAssembler:
            async def feed(self, stream_key: str, entry: dict):
                received.append((stream_key, entry))

        alias_cache = AliasCache(db_path)
        proto = SyslogTCPProtocol(MockAssembler(), alias_cache)

        class MockTransport:
            def get_extra_info(self, name):
                return ("10.0.0.8", 54321)
            def close(self):
                pass

        proto.connection_made(MockTransport())

        msg1 = b"<14>Jan  1 10:00:00 host1 app1: msg 1"
        msg2 = b"<14>Jan  1 10:00:01 host1 app1: msg 2"
        # Back-to-back without newlines
        batch1 = f"{len(msg1)} ".encode("ascii") + msg1 + f"{len(msg2)} ".encode("ascii") + msg2
        proto.data_received(batch1)
        await asyncio.sleep(0.05)

        assert len(received) == 2
        assert received[0][1]["message"] == "msg 1"
        assert received[1][1]["message"] == "msg 2"

        # Trailing / interleaved newlines
        msg3 = b"<14>Jan  1 10:00:02 host1 app1: msg 3"
        batch2 = b"\n" + f"{len(msg3)} ".encode("ascii") + msg3 + b"\r\n"
        proto.data_received(batch2)
        await asyncio.sleep(0.05)

        assert len(received) == 3
        assert received[2][1]["message"] == "msg 3"
        await proto.stop()

    @pytest.mark.asyncio
    async def test_tcp_syslog_octet_counted_fragmented_frames(self, db_path: Path):
        """TCP protocol properly handles octet-counted frames split across multiple network packets."""
        received = []

        class MockAssembler:
            async def feed(self, stream_key: str, entry: dict):
                received.append((stream_key, entry))

        alias_cache = AliasCache(db_path)
        proto = SyslogTCPProtocol(MockAssembler(), alias_cache)

        class MockTransport:
            def get_extra_info(self, name):
                return ("10.0.0.8", 54321)
            def close(self):
                pass

        proto.connection_made(MockTransport())

        msg = b"<14>Jan  1 10:00:00 host1 app1: long message arriving in fragments"
        frame = f"{len(msg)} ".encode("ascii") + msg

        # Split 1: length prefix split
        proto.data_received(frame[:2])
        await asyncio.sleep(0.02)
        assert len(received) == 0

        # Split 2: payload body split
        proto.data_received(frame[2:20])
        await asyncio.sleep(0.02)
        assert len(received) == 0

        # Split 3: remainder
        proto.data_received(frame[20:])
        await asyncio.sleep(0.05)

        assert len(received) == 1
        assert received[0][1]["message"] == "long message arriving in fragments"
        await proto.stop()

    @pytest.mark.asyncio
    async def test_tcp_syslog_octet_counted_absurd_length_disconnects(self, db_path: Path):
        """TCP protocol disconnects and clears buffer if octet-counted frame length exceeds MAX_TCP_BUFFER."""
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

        # Length 99999 exceeds 65536
        proto.data_received(b"99999 <14>message...")

        assert transport.closed is True
        assert proto.buffer == b""
        await proto.stop()

    @pytest.mark.asyncio
    async def test_tcp_syslog_mixed_framing_modes(self, db_path: Path):
        """TCP protocol dynamically handles both octet-counted and newline-delimited frames."""
        received = []

        class MockAssembler:
            async def feed(self, stream_key: str, entry: dict):
                received.append((stream_key, entry))

        alias_cache = AliasCache(db_path)
        proto = SyslogTCPProtocol(MockAssembler(), alias_cache)

        class MockTransport:
            def get_extra_info(self, name):
                return ("10.0.0.8", 54321)
            def close(self):
                pass

        proto.connection_made(MockTransport())

        # First send an octet-counted frame
        msg1 = b"<14>Jan  1 10:00:00 host1 app1: octet message"
        frame1 = f"{len(msg1)} ".encode("ascii") + msg1
        proto.data_received(frame1)

        # Next send a newline-delimited frame
        frame2 = b"<14>Jan  1 10:00:01 host1 app1: newline message\n"
        proto.data_received(frame2)

        await asyncio.sleep(0.05)

        assert len(received) == 2
        assert received[0][1]["message"] == "octet message"
        assert received[1][1]["message"] == "newline message"
        await proto.stop()


# ===================================================================
# 3. Syslog Configuration (SYSLOG_PORT)
# ===================================================================

class TestSyslogConfig:

    def test_syslog_port_default(self, monkeypatch):
        monkeypatch.delenv("SYSLOG_PORT", raising=False)
        assert get_syslog_port() == 1514

    def test_syslog_port_custom_valid(self, monkeypatch):
        monkeypatch.setenv("SYSLOG_PORT", "514")
        assert get_syslog_port() == 514

        monkeypatch.setenv("SYSLOG_PORT", "15140")
        assert get_syslog_port() == 15140

        monkeypatch.setenv("SYSLOG_PORT", " 1514 ")
        assert get_syslog_port() == 1514

        monkeypatch.setenv("SYSLOG_PORT", "1")
        assert get_syslog_port() == 1

        monkeypatch.setenv("SYSLOG_PORT", "65535")
        assert get_syslog_port() == 65535

    def test_syslog_port_invalid_fallback(self, monkeypatch):
        monkeypatch.setenv("SYSLOG_PORT", "0")
        assert get_syslog_port() == 1514

        monkeypatch.setenv("SYSLOG_PORT", "65536")
        assert get_syslog_port() == 1514

        monkeypatch.setenv("SYSLOG_PORT", "-514")
        assert get_syslog_port() == 1514

        monkeypatch.setenv("SYSLOG_PORT", "abc")
        assert get_syslog_port() == 1514

        monkeypatch.setenv("SYSLOG_PORT", "")
        assert get_syslog_port() == 1514


# ===================================================================
# 4. Host Alias Cache & Resolution
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
