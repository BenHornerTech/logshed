"""
Tests for KeyedMultilineAssembler, bounded queue, dropped counter, live ingestion rate tracking, and QueueConsumer.
"""

import asyncio
import concurrent.futures
import datetime
from pathlib import Path
import pytest

import sqlite3
from app.api.deps import run_db_query
from app.core.migrations import get_connection, run_migrations
from app.core import pipeline as pipeline_mod
from app.core.pipeline import (
    InternalLogHandler,
    KeyedMultilineAssembler,
    QueueConsumer,
    IngestionRateTracker,
    _is_continuation,
    get_dropped_count,
    get_ingest_rate,
    get_queue,
    increment_dropped_count,
    record_ingest,
    reset_ingest_rate,
)
from app.core.sse import sse_manager
from app.collectors.docker_collector import _make_log_entry


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "pipeline_test.db"
    run_migrations(p)
    return p


@pytest.fixture(autouse=True)
def reset_pipeline_state():
    """Reset the global queue, drop counter, and ingest rate between tests."""
    pipeline_mod._log_queue = None
    pipeline_mod._dropped_logs_total = 0
    reset_ingest_rate()
    yield
    pipeline_mod._log_queue = None
    pipeline_mod._dropped_logs_total = 0
    reset_ingest_rate()


def _make_entry(**overrides) -> dict:
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
# 1. Keyed Multiline Assembly
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
        asm = KeyedMultilineAssembler()
        entry = _make_entry(message="single line")
        await asm.feed("s1:app", entry)
        await asyncio.sleep(0.3)

        q = get_queue()
        assert not q.empty()
        item = q.get_nowait()
        assert item["message"] == "single line"

    @pytest.mark.asyncio
    async def test_multiline_merging(self):
        asm = KeyedMultilineAssembler()

        await asm.feed("s1:app", _make_entry(message="Exception in thread main", severity=3))
        await asm.feed("s1:app", _make_entry(message="  at com.example.Foo.bar(Foo.java:10)", severity=6))
        await asm.feed("s1:app", _make_entry(message="Caused by: IOException", severity=4))

        await asyncio.sleep(0.3)

        q = get_queue()
        assert not q.empty()
        item = q.get_nowait()
        assert "Exception in thread main" in item["message"]
        assert "at com.example.Foo.bar" in item["message"]
        assert "Caused by: IOException" in item["message"]
        assert item["severity"] == 3

    @pytest.mark.asyncio
    async def test_new_header_flushes_previous(self):
        asm = KeyedMultilineAssembler()

        await asm.feed("s1:app", _make_entry(message="First log line"))
        await asm.feed("s1:app", _make_entry(message="Second log line"))

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
# 2. Docker Multiline Assembly
# ===================================================================

class TestDockerMultilineAssembly:

    @pytest.mark.asyncio
    async def test_docker_stream_key_format(self):
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
        asm = KeyedMultilineAssembler()
        stream_key = "docker:container123"

        await asm.feed(stream_key, _make_entry(
            message='Exception in thread "main" java.lang.NullPointerException',
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

        assert "NullPointerException" in item["message"]
        assert "com.example.Main.process" in item["message"]
        assert "Caused by:" in item["message"]
        assert "java.net.Socket.connect" in item["message"]
        assert item["severity"] == 3

    @pytest.mark.asyncio
    async def test_docker_python_traceback_merging(self):
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

        await asyncio.sleep(0.3)

        q = get_queue()
        items = []
        while not q.empty():
            items.append(q.get_nowait())

        assert len(items) == 2
        assert "Traceback" in items[0]["message"]
        assert "main.py" in items[0]["message"]
        assert items[1]["message"].startswith("ValueError")

    @pytest.mark.asyncio
    async def test_docker_separate_containers_isolated(self):
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

        b_items = [i for i in items if i["app_name"] == "redis"]
        assert len(b_items) == 1
        assert b_items[0]["message"] == "Error from container B"

        a_items = [i for i in items if i["app_name"] == "nginx"]
        assert len(a_items) == 1
        assert "Error from container A" in a_items[0]["message"]
        assert "detail line for A" in a_items[0]["message"]

    def test_docker_source_alias(self):
        entry = _make_log_entry("my-nginx", "abc123", "Started server")
        assert entry["source_alias"] == "docker"
        assert entry["app_name"] == "my-nginx"
        assert entry["source_ip"] == "docker"


# ===================================================================
# 3. Bounded Queue & Dropped Counter
# ===================================================================

class TestBoundedQueueAndDrops:

    @pytest.mark.asyncio
    async def test_queue_maxsize(self):
        q = get_queue()
        assert q.maxsize == 10000

    @pytest.mark.asyncio
    async def test_drop_counter_on_saturation(self):
        q = get_queue()

        for i in range(10000):
            q.put_nowait(_make_entry(message=f"msg{i}"))

        assert q.full()

        asm = KeyedMultilineAssembler()
        for i in range(5):
            await asm.feed(f"k{i}:app", _make_entry(message=f"dropped{i}"))
        await asm.flush_all()

        assert get_dropped_count() == 5

    @pytest.mark.asyncio
    async def test_no_drops_when_space_available(self):
        asm = KeyedMultilineAssembler()
        for i in range(10):
            await asm.feed(f"k{i}:app", _make_entry(message=f"ok{i}"))
        await asm.flush_all()

        assert get_dropped_count() == 0
        q = get_queue()
        assert q.qsize() == 10

    def test_drop_counter_thread_safety(self):
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


# ===================================================================
# 4. Ingestion Rate Tracking
# ===================================================================

class TestIngestionRateTracking:

    def test_rate_calculation_rolling_window(self):
        reset_ingest_rate()
        assert get_ingest_rate() == 0.0

        record_ingest(10)
        assert get_ingest_rate() == 2.0

        record_ingest(15)
        assert get_ingest_rate() == 5.0

        reset_ingest_rate()
        assert get_ingest_rate() == 0.0

    def test_rate_tracker_window_pruning(self):
        tracker = IngestionRateTracker(window_seconds=1.0)
        tracker.record(10)
        assert tracker.get_rate() == 10.0

        with tracker._lock:
            old_time, count = tracker._samples.popleft()
            tracker._samples.append((old_time - 2.0, count))

        assert tracker.get_rate() == 0.0

    def test_concurrent_ingest_recording(self):
        reset_ingest_rate()
        tracker = IngestionRateTracker(window_seconds=5.0)
        num_threads = 10
        records_per_thread = 20

        def _worker():
            for _ in range(records_per_thread):
                tracker.record(5)

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(_worker) for _ in range(num_threads)]
            concurrent.futures.wait(futures)

        assert tracker.get_rate() == 200.0


# ===================================================================
# 5. Queue Consumer
# ===================================================================

class TestQueueConsumer:

    @pytest.mark.asyncio
    async def test_queue_consumer_deadline_drain(self, db_path: Path):
        consumer = QueueConsumer(db_path)
        q = get_queue()

        for i in range(100):
            q.put_nowait(_make_entry(message=f"consumer_test_{i}"))

        consumer_task = asyncio.create_task(consumer.run())

        for _ in range(20):
            if q.empty():
                break
            await asyncio.sleep(0.05)

        assert q.empty()
        await consumer.stop()
        await consumer_task

        conn = get_connection(db_path)
        count = conn.execute("SELECT COUNT(*) FROM logs WHERE message LIKE 'consumer_test_%'").fetchone()[0]
        conn.close()
        assert count == 100

    @pytest.mark.asyncio
    async def test_queue_consumer_populates_entry_id_and_broadcasts(self, db_path: Path):
        consumer = QueueConsumer(db_path)
        q = get_queue()
        sse_q = await sse_manager.subscribe()

        try:
            entry = _make_entry(message="test_log_with_id_broadcast")
            assert "id" not in entry
            q.put_nowait(entry)

            consumer_task = asyncio.create_task(consumer.run())

            received_entry = await asyncio.wait_for(sse_q.get(), timeout=3.5)
            assert "id" in received_entry
            assert isinstance(received_entry["id"], int)
            assert received_entry["id"] > 0
            assert received_entry["message"] == "test_log_with_id_broadcast"

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
    async def test_queue_consumer_batch_insert_retry_succeeds(self, db_path: Path):
        """Transient SQLite error during _insert_batch is retried with backoff and succeeds without dropping logs."""
        consumer = QueueConsumer(db_path)
        q = get_queue()

        for i in range(5):
            q.put_nowait(_make_entry(message=f"retry_test_{i}"))

        original_insert = consumer._insert_batch
        attempts = 0

        def _flaky_insert(batch):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise sqlite3.OperationalError("database is locked")
            return original_insert(batch)

        consumer._insert_batch = _flaky_insert

        consumer_task = asyncio.create_task(consumer.run())

        # Allow consumer to reach batch deadline (2.0s) and retry to succeed
        for _ in range(50):
            if attempts >= 3:
                break
            await asyncio.sleep(0.1)

        await consumer.stop()
        await consumer_task

        assert attempts == 3
        assert get_dropped_count() == 0

        conn = get_connection(db_path)
        count = conn.execute("SELECT COUNT(*) FROM logs WHERE message LIKE 'retry_test_%'").fetchone()[0]
        conn.close()
        assert count == 5

    @pytest.mark.asyncio
    async def test_queue_consumer_retries_without_dropping_and_does_not_drain_queue(self, db_path: Path):
        """
        When database is locked or failing, QueueConsumer retains the batch in memory,
        retries without incrementing dropped log counter, and does NOT pop subsequent queue items.
        """
        consumer = QueueConsumer(db_path)
        q = get_queue()

        # Enqueue first batch
        q.put_nowait(_make_entry(message="batch_1_msg"))

        attempts = 0

        def _failing_insert(batch):
            nonlocal attempts
            attempts += 1
            raise sqlite3.OperationalError("database is locked")

        consumer._insert_batch = _failing_insert

        consumer_task = asyncio.create_task(consumer.run())

        # Wait for batch deadline (2.0s) and first failure
        for _ in range(35):
            if attempts >= 1:
                break
            await asyncio.sleep(0.1)

        assert attempts >= 1

        # While consumer is retrying batch 1, enqueue a second item
        q.put_nowait(_make_entry(message="batch_2_msg"))

        # Wait during retry backoff
        await asyncio.sleep(0.3)

        # batch_2_msg must NOT have been popped/drained from queue
        assert q.qsize() == 1
        assert get_dropped_count() == 0

        # Now signal shutdown
        await consumer.stop()
        await consumer_task

        # Dropped logs count must still be 0 (no batch was dropped permanently)
        assert get_dropped_count() == 0

    @pytest.mark.asyncio
    async def test_run_db_query_closes_connection(self, db_path: Path):
        """run_db_query must explicitly close the database connection in a finally block."""
        from unittest.mock import patch, MagicMock
        import app.api.deps as deps_mod

        spied_conn = None
        orig_get_conn = deps_mod.get_connection

        def _get_wrapped_conn(p):
            nonlocal spied_conn
            conn = orig_get_conn(p)
            spied_conn = MagicMock(wraps=conn)
            return spied_conn

        with patch.object(deps_mod, "get_connection", side_effect=_get_wrapped_conn):
            result = await run_db_query(lambda conn: conn.execute("SELECT 1").fetchone()[0], custom_db_path=db_path)

        assert result == 1
        assert spied_conn is not None
        spied_conn.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_db_query_closes_connection_on_exception(self, db_path: Path):
        """run_db_query must explicitly close the database connection even if query raises."""
        from unittest.mock import patch, MagicMock
        import app.api.deps as deps_mod

        spied_conn = None
        orig_get_conn = deps_mod.get_connection

        def _get_wrapped_conn(p):
            nonlocal spied_conn
            conn = orig_get_conn(p)
            spied_conn = MagicMock(wraps=conn)
            return spied_conn

        def _failing_query(conn: sqlite3.Connection):
            raise RuntimeError("query error")

        with patch.object(deps_mod, "get_connection", side_effect=_get_wrapped_conn):
            with pytest.raises(RuntimeError, match="query error"):
                await run_db_query(_failing_query, custom_db_path=db_path)

        assert spied_conn is not None
        spied_conn.close.assert_called_once()

    def test_internal_log_handler_suppresses_db_loop_loggers(self):
        """InternalLogHandler must ignore logs from pipeline, retention, and storage_metrics."""
        handler = InternalLogHandler()
        import logging

        q = get_queue()
        assert q.empty()

        for name in [
            "app.core.pipeline",
            "app.services.retention",
            "app.services.storage_metrics",
            "app.core.pipeline.submodule",
            "uvicorn.access",
            "httpx",
        ]:
            record = logging.LogRecord(
                name=name,
                level=logging.ERROR,
                pathname="test.py",
                lineno=1,
                msg=f"Error in {name}",
                args=(),
                exc_info=None,
            )
            handler.emit(record)

        # None of the above should have been enqueued
        assert q.empty()

    @pytest.mark.asyncio
    async def test_queue_consumer_normalizes_timezone_and_clamps_future_timestamps(self, db_path: Path):
        """
        Entries with non-UTC timezone offsets, naive timestamps, or future dates are
        normalized to UTC and clamped so that newer logs always sort ahead of older logs.
        """
        consumer = QueueConsumer(db_path)
        q = get_queue()

        # Older log emitted at 17:11 UTC with +01:00 (18:11 local)
        q.put_nowait({
            "timestamp": "2026-09-05T18:11:00+01:00",
            "received_at": "2026-09-05T17:11:00.000000+00:00",
            "source_ip": "192.168.1.1",
            "source_alias": "gateway",
            "app_name": "syslog",
            "facility": 1,
            "severity": 6,
            "message": "Older 18:11 BST log with +01:00",
            "raw": "raw",
        })

        # Newer log emitted at 17:29 UTC (18:29 local)
        q.put_nowait({
            "timestamp": "2026-09-05T17:29:00+00:00",
            "received_at": "2026-09-05T17:29:00.000000+00:00",
            "source_ip": "docker",
            "source_alias": "docker",
            "app_name": "nginx",
            "facility": 1,
            "severity": 6,
            "message": "Newer 18:29 BST log in UTC",
            "raw": "raw",
        })

        consumer_task = asyncio.create_task(consumer.run())

        for _ in range(20):
            if q.empty():
                break
            await asyncio.sleep(0.05)

        assert q.empty()
        await consumer.stop()
        await consumer_task

        conn = get_connection(db_path)
        rows = conn.execute(
            "SELECT message, timestamp FROM logs ORDER BY timestamp DESC, id DESC"
        ).fetchall()
        conn.close()

        assert len(rows) == 2
        # The 17:29 log MUST sort before the 18:11+01:00 (17:11 UTC) log
        assert "Newer 18:29 BST" in rows[0][0]
        assert "Older 18:11 BST" in rows[1][0]
        assert rows[0][1] > rows[1][1]

    @pytest.mark.asyncio
    async def test_shutdown_queue_drain_preserves_all_logs(self, db_path: Path):
        """
        During application shutdown, QueueConsumer.stop() signals shutdown, drains all remaining
        items in _log_queue, commits them to SQLite in final batch(es), and drops 0 logs.
        """
        consumer = QueueConsumer(db_path)
        q = get_queue()

        # Enqueue 150 items into the shared queue
        for i in range(150):
            q.put_nowait(_make_entry(message=f"shutdown_drain_msg_{i}"))

        # Start consumer and immediately stop it to simulate SIGTERM during active queue
        consumer_task = asyncio.create_task(consumer.run())
        await consumer.stop()
        await consumer_task

        # Queue must be completely drained
        assert q.empty()

        # All 150 items must be safely committed to SQLite
        conn = get_connection(db_path)
        count = conn.execute("SELECT COUNT(*) FROM logs WHERE message LIKE 'shutdown_drain_msg_%'").fetchone()[0]
        conn.close()
        assert count == 150
        assert get_dropped_count() == 0

    @pytest.mark.asyncio
    async def test_shutdown_queue_drain_when_consumer_not_running(self, db_path: Path):
        """
        If QueueConsumer.run() was not running or already stopped, calling stop() directly drains
        any remaining items in _log_queue into SQLite.
        """
        consumer = QueueConsumer(db_path)
        q = get_queue()

        for i in range(30):
            q.put_nowait(_make_entry(message=f"unstarted_drain_msg_{i}"))

        # consumer.run() was never started
        await consumer.stop()

        assert q.empty()
        conn = get_connection(db_path)
        count = conn.execute("SELECT COUNT(*) FROM logs WHERE message LIKE 'unstarted_drain_msg_%'").fetchone()[0]
        conn.close()
        assert count == 30
        assert get_dropped_count() == 0

    @pytest.mark.asyncio
    async def test_low_rate_ingestion_flushes_with_low_latency_without_two_second_delay(self, db_path: Path):
        """
        In low-rate ingestion, QueueConsumer commits the batch after the brief debounce window (e.g. 50ms)
        rather than waiting for a full 2.0-second timeout.
        """
        import time

        consumer = QueueConsumer(db_path, debounce_seconds=0.05)
        consumer_task = asyncio.create_task(consumer.run())
        q = get_queue()

        # Record monotonic time right before putting single log
        start_time = time.monotonic()
        q.put_nowait(_make_entry(message="low_latency_single_log"))

        # Poll database for row to appear
        found = False
        elapsed = 0.0
        for _ in range(50):
            await asyncio.sleep(0.02)
            elapsed = time.monotonic() - start_time
            conn = get_connection(db_path)
            row = conn.execute("SELECT id FROM logs WHERE message = 'low_latency_single_log'").fetchone()
            conn.close()
            if row is not None:
                found = True
                break

        await consumer.stop()
        await consumer_task

        assert found is True
        # Latency should be well under the previous 2.0-second delay (typically < 0.3s)
        assert elapsed < 0.6

    @pytest.mark.asyncio
    async def test_shutdown_queue_drain_retries_transient_sqlite_lock(self, db_path: Path):
        """
        If SQLite encounters a transient OperationalError during shutdown drain,
        QueueConsumer retries and successfully commits pending logs without drops.
        """
        import sqlite3

        consumer = QueueConsumer(db_path)
        q = get_queue()

        for i in range(25):
            q.put_nowait(_make_entry(message=f"transient_lock_msg_{i}"))

        orig_insert = consumer._insert_batch
        attempts = 0

        def flaky_insert(batch):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise sqlite3.OperationalError("database is locked")
            return orig_insert(batch)

        consumer._insert_batch = flaky_insert

        await consumer.stop()

        assert attempts >= 2
        assert q.empty()

        conn = get_connection(db_path)
        count = conn.execute(
            "SELECT COUNT(*) FROM logs WHERE message LIKE 'transient_lock_msg_%'"
        ).fetchone()[0]
        conn.close()

        assert count == 25
        assert get_dropped_count() == 0

    @pytest.mark.asyncio
    async def test_shutdown_queue_drain_exits_cleanly_when_db_permanently_fails(self, db_path: Path):
        """
        If database fails permanently during shutdown, all items are marked done,
        queue is cleared, and the consumer exits cleanly without deadlocking.
        """
        import sqlite3

        consumer = QueueConsumer(db_path)
        q = get_queue()

        for i in range(12):
            q.put_nowait(_make_entry(message=f"perm_fail_msg_{i}"))

        def broken_insert(batch):
            raise sqlite3.OperationalError("disk I/O error")

        consumer._insert_batch = broken_insert

        # stop() must not hang or deadlock even if DB writes permanently fail
        await consumer.stop()

        assert q.empty()

    @pytest.mark.asyncio
    async def test_shutdown_queue_drain_concurrent_stop_calls(self, db_path: Path):
        """
        Multiple concurrent consumer.stop() calls must execute safely without race conditions
        or SQLite lock contention.
        """
        consumer = QueueConsumer(db_path)
        consumer_task = asyncio.create_task(consumer.run())
        q = get_queue()

        for i in range(40):
            q.put_nowait(_make_entry(message=f"concurrent_stop_msg_{i}"))

        # Launch concurrent stop() calls
        await asyncio.gather(
            consumer.stop(),
            consumer.stop(),
            consumer.stop(),
        )
        await consumer_task

        assert q.empty()
        conn = get_connection(db_path)
        count = conn.execute(
            "SELECT COUNT(*) FROM logs WHERE message LIKE 'concurrent_stop_msg_%'"
        ).fetchone()[0]
        conn.close()
        assert count == 40

