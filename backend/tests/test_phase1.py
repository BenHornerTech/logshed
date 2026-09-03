"""
Phase 1 verification tests for LogShed.

Covers:
  - RFC 3164 / RFC 5424 syslog parsing
  - Multiline log assembly (KeyedMultilineAssembler)
  - FTS5 external-content sync on INSERT and DELETE (no orphan records)
  - Bounded queue drop counter behaviour when saturated
  - storage_metrics row written on manual sampling trigger with correct bytes
"""

import asyncio
import datetime
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from app.core.migrations import get_connection, run_migrations
from app.core import pipeline as pipeline_mod
from app.core.pipeline import (
    KeyedMultilineAssembler,
    QueueConsumer,
    _is_continuation,
    get_dropped_count,
    get_queue,
)
from app.collectors.syslog import AliasCache, SyslogTCPProtocol, parse_syslog_message
from app.core.sse import sse_manager
from app.services.storage_metrics import (
    record_metrics,
    sample_storage_metrics,
    prune_old_metrics,
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
        "source_ip": "10.0.0.1",
        "source_alias": "test-host",
        "app_name": "test-app",
        "facility": 1,
        "severity": 6,
        "message": "hello world",
        "raw": "<14>Jan  5 10:30:00 test-host test-app: hello world",
    }
    base.update(overrides)
    return base


# ===================================================================
# 1. RFC 3164 / RFC 5424 syslog parsing
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
        # Starts with number after PRI and timestamp, but is RFC 3164
        raw = b"<134>Jan 15 10:30:00 srv01 myapp: 42 connections opened"
        result = parse_syslog_message(raw, "10.0.0.1")

        assert result["facility"] == 16
        assert result["severity"] == 6
        assert result["app_name"] == "myapp"
        assert result["message"] == "42 connections opened"

    def test_unparseable_fallback(self):
        raw = b"This is not a syslog message at all"
        result = parse_syslog_message(raw, "10.0.0.1")

        assert result["severity"] == 6      # default Info
        assert result["facility"] == 1      # default
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
        # Should be a valid ISO timestamp
        datetime.datetime.fromisoformat(result["received_at"])

    def test_raw_preserved(self):
        raw = b"<14>Jan  1 00:00:00 h app: msg"
        result = parse_syslog_message(raw, "10.0.0.1")
        assert result["raw"] == raw.decode("utf-8").strip()

    def test_utf8_with_errors(self):
        raw = b"<14>Jan  1 00:00:00 h app: hello \xff world"
        result = parse_syslog_message(raw, "10.0.0.1")
        # Should not raise; replacement character expected
        assert "\ufffd" in result["raw"] or "world" in result["raw"]


# ===================================================================
# 2. Multiline assembly
# ===================================================================

class TestMultilineAssembly:

    def test_continuation_detection(self):
        assert _is_continuation("  indented line") is True
        assert _is_continuation("\ttabbed line") is True
        assert _is_continuation("Caused by: NullPointerException") is True
        assert _is_continuation("Traceback (most recent call last):") is True
        assert _is_continuation("at com.example.Main.run(Main.java:42)") is True
        assert _is_continuation("... 15 more") is True
        assert _is_continuation("Normal log line") is False
        assert _is_continuation("") is False

    @pytest.mark.asyncio
    async def test_single_line_flushes(self):
        """A single non-continuation line should flush after 150ms timeout."""
        asm = KeyedMultilineAssembler()
        entry = _make_entry(message="single line")
        await asm.feed("s1:app", entry)
        await asyncio.sleep(0.3)  # wait past 150ms

        q = get_queue()
        assert not q.empty()
        item = q.get_nowait()
        assert item["message"] == "single line"

    @pytest.mark.asyncio
    async def test_multiline_merging(self):
        """Continuation lines merge into the preceding entry."""
        asm = KeyedMultilineAssembler()

        await asm.feed("s1:app", _make_entry(message="Exception in thread main", severity=3))
        await asm.feed("s1:app", _make_entry(message="  at com.example.Foo.bar(Foo.java:10)", severity=6))
        await asm.feed("s1:app", _make_entry(message="Caused by: IOException", severity=4))

        await asyncio.sleep(0.3)  # wait for flush timeout

        q = get_queue()
        assert not q.empty()
        item = q.get_nowait()
        assert "Exception in thread main" in item["message"]
        assert "at com.example.Foo.bar" in item["message"]
        assert "Caused by: IOException" in item["message"]
        # Severity should be minimum (most severe = lowest number)
        assert item["severity"] == 3

    @pytest.mark.asyncio
    async def test_new_header_flushes_previous(self):
        """A new non-continuation line for the same stream flushes the previous buffer."""
        asm = KeyedMultilineAssembler()

        await asm.feed("s1:app", _make_entry(message="First log line"))
        await asm.feed("s1:app", _make_entry(message="Second log line"))  # not a continuation → flush first

        await asyncio.sleep(0.3)

        q = get_queue()
        items = []
        while not q.empty():
            items.append(q.get_nowait())

        assert len(items) == 2
        assert items[0]["message"] == "First log line"
        assert items[1]["message"] == "Second log line"

    @pytest.mark.asyncio
    async def test_flush_all(self):
        """flush_all should drain all streams."""
        asm = KeyedMultilineAssembler()

        await asm.feed("s1:app", _make_entry(message="stream1"))
        await asm.feed("s2:app", _make_entry(message="stream2"))
        await asm.flush_all()

        q = get_queue()
        items = []
        while not q.empty():
            items.append(q.get_nowait())
        assert len(items) == 2


# ===================================================================
# 3. FTS5 external-content sync on INSERT and DELETE
# ===================================================================

class TestFTS5Sync:

    def test_insert_syncs_to_fts(self, db_path: Path):
        """INSERT into logs should auto-populate logs_fts via trigger."""
        conn = get_connection(db_path)
        try:
            conn.execute(
                """INSERT INTO logs (timestamp, received_at, source_ip, source_alias,
                   app_name, facility, severity, message, raw)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("2024-01-15T10:00:00", "2024-01-15T10:00:00",
                 "10.0.0.1", "myhost", "nginx", 1, 6, "GET /index.html 200", "raw line"),
            )
            conn.commit()

            # Verify FTS search finds it
            rows = conn.execute(
                "SELECT rowid FROM logs_fts WHERE logs_fts MATCH 'nginx'"
            ).fetchall()
            assert len(rows) == 1

            rows = conn.execute(
                "SELECT rowid FROM logs_fts WHERE logs_fts MATCH '\"GET\"'"
            ).fetchall()
            assert len(rows) == 1
        finally:
            conn.close()

    def test_delete_removes_from_fts(self, db_path: Path):
        """DELETE from logs should remove from logs_fts via trigger — no orphans."""
        conn = get_connection(db_path)
        try:
            conn.execute(
                """INSERT INTO logs (timestamp, received_at, source_ip, source_alias,
                   app_name, facility, severity, message, raw)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("2024-01-15T10:00:00", "2024-01-15T10:00:00",
                 "10.0.0.1", "myhost", "sshd", 1, 4, "Failed password for root", "raw"),
            )
            conn.commit()

            # Confirm in FTS
            rows = conn.execute(
                "SELECT rowid FROM logs_fts WHERE logs_fts MATCH 'sshd'"
            ).fetchall()
            assert len(rows) == 1

            # Delete the log entry
            conn.execute("DELETE FROM logs WHERE app_name = 'sshd'")
            conn.commit()

            # Verify FTS is clean — no orphan records
            rows = conn.execute(
                "SELECT rowid FROM logs_fts WHERE logs_fts MATCH 'sshd'"
            ).fetchall()
            assert len(rows) == 0
        finally:
            conn.close()

    def test_update_syncs_fts(self, db_path: Path):
        """UPDATE should remove old FTS entry and add new one."""
        conn = get_connection(db_path)
        try:
            conn.execute(
                """INSERT INTO logs (timestamp, received_at, source_ip, source_alias,
                   app_name, facility, severity, message, raw)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("2024-01-15T10:00:00", "2024-01-15T10:00:00",
                 "10.0.0.1", "myhost", "app1", 1, 6, "old message content", "raw"),
            )
            conn.commit()

            # Update the message
            conn.execute(
                "UPDATE logs SET message = 'new message content', app_name = 'app2' WHERE app_name = 'app1'"
            )
            conn.commit()

            # Old content should NOT be findable
            rows = conn.execute(
                "SELECT rowid FROM logs_fts WHERE logs_fts MATCH 'app1'"
            ).fetchall()
            assert len(rows) == 0

            # New content SHOULD be findable
            rows = conn.execute(
                "SELECT rowid FROM logs_fts WHERE logs_fts MATCH 'app2'"
            ).fetchall()
            assert len(rows) == 1
        finally:
            conn.close()

    def test_multiple_inserts_and_deletes(self, db_path: Path):
        """Batch INSERT followed by partial DELETE should keep FTS consistent."""
        conn = get_connection(db_path)
        try:
            for i in range(10):
                conn.execute(
                    """INSERT INTO logs (timestamp, received_at, source_ip, source_alias,
                       app_name, facility, severity, message, raw)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    ("2024-01-15T10:00:00", "2024-01-15T10:00:00",
                     "10.0.0.1", "myhost", f"app{i}", 1, 6,
                     f"Message number {i} with unique_token_{i}", "raw"),
                )
            conn.commit()

            # Delete odd-numbered entries
            for i in range(1, 10, 2):
                conn.execute(f"DELETE FROM logs WHERE app_name = 'app{i}'")
            conn.commit()

            # Even entries should still be searchable
            for i in range(0, 10, 2):
                rows = conn.execute(
                    f"SELECT rowid FROM logs_fts WHERE logs_fts MATCH 'unique_token_{i}'"
                ).fetchall()
                assert len(rows) == 1, f"Expected app{i} in FTS"

            # Odd entries should be gone
            for i in range(1, 10, 2):
                rows = conn.execute(
                    f"SELECT rowid FROM logs_fts WHERE logs_fts MATCH 'unique_token_{i}'"
                ).fetchall()
                assert len(rows) == 0, f"Expected app{i} NOT in FTS (orphan!)"
        finally:
            conn.close()


# ===================================================================
# 4. Bounded queue drop counter
# ===================================================================

class TestBoundedQueue:

    @pytest.mark.asyncio
    async def test_queue_maxsize(self):
        """Queue should have maxsize 10000."""
        q = get_queue()
        assert q.maxsize == 10000

    @pytest.mark.asyncio
    async def test_drop_counter_on_saturation(self):
        """When the queue is full, drops should be counted."""
        q = get_queue()

        # Fill the queue
        for i in range(10000):
            q.put_nowait(_make_entry(message=f"msg{i}"))

        assert q.full()

        # These should be dropped
        asm = KeyedMultilineAssembler()
        for i in range(5):
            await asm.feed(f"k{i}:app", _make_entry(message=f"dropped{i}"))
        # Flush all to push into the full queue
        await asm.flush_all()

        assert get_dropped_count() == 5

    @pytest.mark.asyncio
    async def test_no_drops_when_space_available(self):
        """No drops when the queue has space."""
        asm = KeyedMultilineAssembler()
        for i in range(10):
            await asm.feed(f"k{i}:app", _make_entry(message=f"ok{i}"))
        await asm.flush_all()

        assert get_dropped_count() == 0
        q = get_queue()
        assert q.qsize() == 10

    @pytest.mark.asyncio
    async def test_queue_consumer_deadline_drain(self, db_path: Path):
        """QueueConsumer should aggressively drain available items without unconditional 2s sleep."""
        consumer = QueueConsumer(db_path)
        q = get_queue()

        # Enqueue 100 items
        for i in range(100):
            q.put_nowait(_make_entry(message=f"consumer_test_{i}"))

        consumer_task = asyncio.create_task(consumer.run())

        # Give it a short moment to process all 100 items immediately
        for _ in range(20):
            if q.empty():
                break
            await asyncio.sleep(0.05)

        assert q.empty(), "QueueConsumer should have aggressively drained all 100 items"
        await consumer.stop()
        await consumer_task

        # Verify items in DB
        conn = get_connection(db_path)
        count = conn.execute("SELECT COUNT(*) FROM logs WHERE message LIKE 'consumer_test_%'").fetchone()[0]
        conn.close()
        assert count == 100

    @pytest.mark.asyncio
    async def test_queue_consumer_populates_entry_id_and_broadcasts(self, db_path: Path):
        """QueueConsumer should populate generated row id on entry and broadcast with valid integer id."""
        consumer = QueueConsumer(db_path)
        q = get_queue()
        sse_q = await sse_manager.subscribe()

        try:
            entry = _make_entry(message="test_log_with_id_broadcast")
            assert "id" not in entry
            q.put_nowait(entry)

            consumer_task = asyncio.create_task(consumer.run())

            # Wait for item to be processed and received over SSE queue (consumer has 2.0s batch window)
            received_entry = await asyncio.wait_for(sse_q.get(), timeout=3.5)
            assert "id" in received_entry
            assert isinstance(received_entry["id"], int)
            assert received_entry["id"] > 0
            assert received_entry["message"] == "test_log_with_id_broadcast"

            # Verify the id matches the row in DB
            conn = get_connection(db_path)
            row = conn.execute("SELECT id, message FROM logs WHERE id = ?", (received_entry["id"],)).fetchone()
            conn.close()
            assert row is not None
            assert row[0] == received_entry["id"]
            assert row[1] == "test_log_with_id_broadcast"

            await consumer.stop()
            await consumer_task
        finally:
            await sse_manager.unsubscribe(sse_q)

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

        # Send 70 KB of data without newline
        oversized_chunk = b"A" * (70 * 1024)
        proto.data_received(oversized_chunk)

        assert transport.closed is True, "Transport must be closed when buffer exceeds 64KB without newline"
        assert proto.buffer == b"", "Buffer must be cleared after limit exceeded"


# ===================================================================
# 5. Storage metrics sampling
# ===================================================================

class TestStorageMetrics:

    def test_sample_returns_correct_keys(self, db_path: Path):
        """sample_storage_metrics should return all expected keys."""
        metrics = sample_storage_metrics(db_path)
        assert "recorded_at" in metrics
        assert "db_size_bytes" in metrics
        assert "disk_free_bytes" in metrics
        assert "disk_total_bytes" in metrics
        assert "total_logs_count" in metrics

    def test_db_size_bytes_positive(self, db_path: Path):
        """DB file should have a positive size after migration."""
        metrics = sample_storage_metrics(db_path)
        assert metrics["db_size_bytes"] > 0

    def test_disk_values_sane(self, db_path: Path):
        """Disk total should be > free, and both should be > 0."""
        metrics = sample_storage_metrics(db_path)
        assert metrics["disk_total_bytes"] > 0
        assert metrics["disk_free_bytes"] >= 0
        assert metrics["disk_total_bytes"] >= metrics["disk_free_bytes"]

    def test_total_logs_count_zero_initially(self, db_path: Path):
        """No logs inserted yet, count should be 0."""
        metrics = sample_storage_metrics(db_path)
        assert metrics["total_logs_count"] == 0

    def test_total_logs_count_after_inserts(self, db_path: Path):
        """Count should reflect inserted log rows."""
        conn = get_connection(db_path)
        for i in range(5):
            conn.execute(
                """INSERT INTO logs (timestamp, received_at, source_ip, source_alias,
                   app_name, facility, severity, message, raw)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("2024-01-15T10:00:00", "2024-01-15T10:00:00",
                 "10.0.0.1", "myhost", "app", 1, 6, f"msg{i}", "raw"),
            )
        conn.commit()
        conn.close()

        metrics = sample_storage_metrics(db_path)
        assert metrics["total_logs_count"] == 5

    def test_record_metrics_writes_row(self, db_path: Path):
        """record_metrics should insert a row into storage_metrics."""
        metrics = record_metrics(db_path)

        conn = get_connection(db_path)
        rows = conn.execute("SELECT * FROM storage_metrics").fetchall()
        conn.close()

        assert len(rows) == 1
        # Verify values match
        row = rows[0]
        assert row[1] == metrics["recorded_at"]   # recorded_at
        assert row[2] == metrics["db_size_bytes"]  # db_size_bytes
        assert row[3] == metrics["disk_free_bytes"]
        assert row[4] == metrics["disk_total_bytes"]
        assert row[5] == metrics["total_logs_count"]

    def test_prune_old_metrics(self, db_path: Path):
        """prune_old_metrics should delete records older than 30 days."""
        conn = get_connection(db_path)
        # Insert an old record (31 days ago)
        old_time = (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=31)
        ).isoformat()
        conn.execute(
            """INSERT INTO storage_metrics 
            (recorded_at, db_size_bytes, disk_free_bytes, disk_total_bytes, total_logs_count)
            VALUES (?, ?, ?, ?, ?)""",
            (old_time, 1000, 2000, 3000, 0),
        )
        # Insert a recent record
        recent_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO storage_metrics 
            (recorded_at, db_size_bytes, disk_free_bytes, disk_total_bytes, total_logs_count)
            VALUES (?, ?, ?, ?, ?)""",
            (recent_time, 1000, 2000, 3000, 0),
        )
        conn.commit()
        conn.close()

        deleted = prune_old_metrics(db_path)
        assert deleted == 1

        conn = get_connection(db_path)
        remaining = conn.execute("SELECT COUNT(*) FROM storage_metrics").fetchone()[0]
        conn.close()
        assert remaining == 1


# ===================================================================
# 6. Schema integrity checks
# ===================================================================

class TestSchemaIntegrity:

    def test_user_version_set(self, db_path: Path):
        """After migration, user_version should be 1."""
        conn = get_connection(db_path)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()
        assert version >= 1

    def test_wal_mode_enabled(self, db_path: Path):
        """WAL journal mode should be active."""
        conn = get_connection(db_path)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert mode.lower() == "wal"

    def test_all_tables_exist(self, db_path: Path):
        """All v1 tables should exist."""
        conn = get_connection(db_path)
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        expected = {"logs", "host_aliases", "storage_metrics", "ai_audit_log",
                    "admin_auth", "system_settings"}
        assert expected.issubset(tables)

    def test_fts_table_exists(self, db_path: Path):
        """logs_fts virtual table should exist."""
        conn = get_connection(db_path)
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        assert "logs_fts" in tables

    def test_triggers_exist(self, db_path: Path):
        """FTS sync triggers should exist."""
        conn = get_connection(db_path)
        triggers = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            ).fetchall()
        }
        conn.close()
        assert {"logs_ai", "logs_ad", "logs_au"}.issubset(triggers)

    def test_indexes_exist(self, db_path: Path):
        """B-tree indexes should exist."""
        conn = get_connection(db_path)
        indexes = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        conn.close()
        expected = {"idx_logs_time_sev", "idx_logs_app_time", "idx_logs_src_time",
                    "idx_storage_metrics_time"}
        assert expected.issubset(indexes)

    def test_admin_auth_single_row_constraint(self, db_path: Path):
        """admin_auth should only allow id=1."""
        conn = get_connection(db_path)
        conn.execute(
            "INSERT INTO admin_auth (id, password_hash, created_at, updated_at) VALUES (1, 'hash', '2024-01-01', '2024-01-01')"
        )
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO admin_auth (id, password_hash, created_at, updated_at) VALUES (2, 'hash2', '2024-01-01', '2024-01-01')"
            )
        conn.close()

    def test_idempotent_migration(self, tmp_path: Path):
        """Running migrations twice should be idempotent."""
        p = tmp_path / "idempotent.db"
        run_migrations(p)
        run_migrations(p)  # Should not raise
        conn = get_connection(p)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()
        assert version >= 1
