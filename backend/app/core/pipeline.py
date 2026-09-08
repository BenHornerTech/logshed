"""
Core log ingestion pipeline components for LogShed.
Provides the shared queue, multiline assembly, and SQLite batch consumer.
"""

import asyncio
import datetime
import logging
import threading
import time
import traceback
from typing import Optional, Union
from pathlib import Path
from collections import defaultdict, deque

from app.core.migrations import get_connection

logger = logging.getLogger(__name__)


class InternalLogHandler(logging.Handler):
    """
    Python logging handler that captures internal application warnings and errors
    and feeds them directly into the LogShed ingestion pipeline.
    Ignores noisy HTTP access logs, SSE broadcast tasks, and ingestion pipeline internals
    to prevent self-referential loops.
    """
    IGNORED_LOGGERS = {
        "uvicorn.access",
        "httpcore",
        "httpx",
        "asyncio",
        "app.core.pipeline",
        "app.core.sse",
        "app.core.migrations",
        "app.services.retention",
        "app.services.storage_metrics",
    }

    def __init__(self, level: Optional[Union[int, str]] = None):
        super().__init__()
        self._thread_local = threading.local()
        self._is_disabled = False
        if level is not None:
            self.set_internal_level(level)
        else:
            from app.core.config import get_internal_log_level
            configured_level = get_internal_log_level()
            if configured_level is None:
                self._is_disabled = True
            else:
                self.setLevel(configured_level)

    def set_internal_level(self, level: Optional[Union[int, str]]) -> None:
        """
        Dynamically update the internal log handler filter level.
        Accepts integer logging levels, string level names ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'),
        or 'DISABLED' / 'OFF' / 'NONE' / None to disable internal log emission.
        """
        if level is None:
            self._is_disabled = True
            return

        from app.core.config import parse_internal_log_level
        parsed = parse_internal_log_level(level)
        if parsed is None:
            self._is_disabled = True
        else:
            self._is_disabled = False
            self.setLevel(parsed)

    @property
    def is_disabled(self) -> bool:
        """True if internal log emission is disabled."""
        return self._is_disabled

    def emit(self, record: logging.LogRecord) -> None:
        if self._is_disabled:
            return

        if (
            record.name in self.IGNORED_LOGGERS
            or record.name.startswith("uvicorn.access")
            or any(record.name.startswith(f"{ignored}.") for ignored in self.IGNORED_LOGGERS)
        ):
            return

        # Check handler level filter (in case emit() is called directly or via custom handler dispatch)
        if self.level and record.levelno < self.level:
            return

        # Re-entrancy guard to prevent recursive logging loops on the same thread
        if getattr(self._thread_local, "in_emit", False):
            return
        self._thread_local.in_emit = True
        try:
            # Map Python log levels to RFC 5424 severity (0-7)
            if record.levelno >= logging.CRITICAL:
                severity = 2
            elif record.levelno >= logging.ERROR:
                severity = 3
            elif record.levelno >= logging.WARNING:
                severity = 4
            elif record.levelno >= logging.INFO:
                severity = 6
            else:
                severity = 7

            msg = record.getMessage()
            if record.exc_info:
                msg += "\n" + "".join(traceback.format_exception(*record.exc_info))

            from app.core.redactor import redact
            msg = redact(msg)

            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            app_subname = record.name.split(".")[-1] if "." in record.name else record.name
            log_entry = {
                "timestamp": now_iso,
                "received_at": now_iso,
                "source_ip": "127.0.0.1",
                "source_alias": "logshed",
                "app_name": app_subname,
                "facility": 1,
                "severity": severity,
                "message": msg,
                "raw": f"[{now_iso}] [{record.name}] [{record.levelname}] {msg}",
            }

            try:
                queue = get_queue()
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None

                if loop and loop.is_running():
                    if threading.current_thread() is threading.main_thread():
                        try:
                            queue.put_nowait(log_entry)
                        except Exception:
                            pass
                    else:
                        def _threadsafe_put(q, entry):
                            try:
                                q.put_nowait(entry)
                            except Exception:
                                pass
                        loop.call_soon_threadsafe(_threadsafe_put, queue, log_entry)
                else:
                    try:
                        queue.put_nowait(log_entry)
                    except Exception:
                        pass
            except Exception:
                pass
        finally:
            self._thread_local.in_emit = False

# Module-level shared state
_log_queue: Optional[asyncio.Queue] = None
_dropped_logs_total: int = 0
_dropped_logs_lock = threading.Lock()
_QUEUE_MAXSIZE = 10000

def get_queue() -> asyncio.Queue:
    """Returns the singleton log queue, creating it if needed."""
    global _log_queue
    if _log_queue is None:
        _log_queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
    return _log_queue

def increment_dropped_count(amount: int = 1) -> None:
    """Thread-safe increment of the dropped logs counter."""
    global _dropped_logs_total
    with _dropped_logs_lock:
        _dropped_logs_total += amount

def get_dropped_count() -> int:
    """Returns the total number of logs dropped due to queue overflow."""
    with _dropped_logs_lock:
        return _dropped_logs_total


class IngestionRateTracker:
    """
    Thread-safe throughput counter calculating instantaneous ingestion rate
    (logs per second) over a rolling window (default 5.0 seconds).
    """
    def __init__(self, window_seconds: float = 5.0):
        self._window = window_seconds
        self._samples: deque[tuple[float, int]] = deque()
        self._lock = threading.Lock()

    def record(self, count: int = 1) -> None:
        """Record ingested log count at current monotonic timestamp."""
        now = time.monotonic()
        with self._lock:
            self._samples.append((now, count))
            self._prune(now)

    def get_rate(self) -> float:
        """Calculate logs per second over the rolling window."""
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            total = sum(c for _, c in self._samples)
            return round(total / self._window, 2)

    def _prune(self, now: float) -> None:
        cutoff = now - self._window
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def reset(self) -> None:
        """Clear all recorded samples."""
        with self._lock:
            self._samples.clear()


_rate_tracker = IngestionRateTracker(window_seconds=5.0)


def record_ingest(count: int = 1) -> None:
    """Thread-safe recording of ingested logs."""
    _rate_tracker.record(count)


def get_ingest_rate() -> float:
    """Returns the instantaneous ingestion rate (logs/sec) over the rolling 5-second window."""
    return _rate_tracker.get_rate()


def reset_ingest_rate() -> None:
    """Resets the ingestion rate tracker (useful for tests)."""
    _rate_tracker.reset()


def _is_continuation(line: str) -> bool:
    """
    Checks if a line is a continuation line for multi-line logs.
    """
    if not line:
        return False
        
    if line[0] in (' ', '\t'):
        return True
        
    lower_line = line.lower()
    if lower_line.startswith("caused by:"):
        return True
    if lower_line.startswith("traceback"):
        return True
    if line.startswith("at "):
        return True
    if line.startswith("..."):
        return True
        
    return False

class KeyedMultilineAssembler:
    """
    Buffers continuation lines keyed by stream_key, flushing assembled
    multi-line entries into the shared queue.
    """
    
    def __init__(self):
        # Maps stream_key to a list of entry dicts buffered so far
        self._buffers: dict[str, list[dict]] = defaultdict(list)
        # Maps stream_key to its flush timer handle
        self._timers: dict[str, asyncio.TimerHandle] = {}
        self._flush_timeout = 0.150  # 150ms

    async def feed(self, stream_key: str, entry: dict) -> None:
        """
        Feed a parsed log entry. Entry dict has keys:
        timestamp, received_at, source_ip, source_alias, app_name,
        facility, severity, message, raw
        """
        message = entry.get('message', '')
        is_cont = _is_continuation(message)
        
        # If it's NOT a continuation, but we have buffered content for this stream,
        # we should flush the existing buffer before starting a new one.
        if not is_cont and self._buffers[stream_key]:
            self._flush_stream_internal(stream_key)
        
        # Add to buffer
        self._buffers[stream_key].append(entry)
        
        # Reset timer
        if stream_key in self._timers:
            self._timers[stream_key].cancel()
            
        loop = asyncio.get_running_loop()
        self._timers[stream_key] = loop.call_later(
            self._flush_timeout, 
            self._flush_stream_internal, 
            stream_key
        )

    def _flush_stream_internal(self, stream_key: str) -> None:
        """
        Internal flush logic.
        Takes buffered lines, merges them, and puts to the shared queue.
        """
        if stream_key in self._timers:
            self._timers[stream_key].cancel()
            del self._timers[stream_key]
            
        buffered = self._buffers.pop(stream_key, None)
        if not buffered:
            return
            
        # Merge buffered entries
        first_entry = buffered[0]
        if len(buffered) == 1:
            merged_entry = first_entry
        else:
            merged_message = "\n".join(e.get('message', '') for e in buffered)
            merged_raw = "\n".join(e.get('raw', '') for e in buffered)
            # Minimum severity is the most severe
            min_severity = min((e.get('severity', 7) for e in buffered))
            
            merged_entry = first_entry.copy()
            merged_entry['message'] = merged_message
            merged_entry['raw'] = merged_raw
            merged_entry['severity'] = min_severity

        queue = get_queue()
        try:
            queue.put_nowait(merged_entry)
        except asyncio.QueueFull:
            increment_dropped_count(1)

    async def flush_all(self) -> None:
        """Flush all streams. Called on shutdown."""
        keys = list(self._buffers.keys())
        for k in keys:
            self._flush_stream_internal(k)

_QUEUE_SENTINEL = object()


class QueueConsumer:
    """
    Background task that drains the shared queue and batch-inserts into SQLite.
    """
    def __init__(self, db_path: str | Path, debounce_seconds: float = 0.05):
        self._db_path = Path(db_path)
        self._debounce_seconds = debounce_seconds
        self._started = False
        self._running = False
        self._stopping = False
        self._stop_event = asyncio.Event()
        self._drain_done = asyncio.Event()
        self._drain_lock = asyncio.Lock()

    async def run(self) -> None:
        """Main loop: drain queue with debounce-based batching (up to 5000 records or debounce window)."""
        self._started = True
        self._running = True
        self._stop_event.clear()
        self._drain_done.clear()
        queue = get_queue()

        try:
            while self._running and not self._stopping:
                # Wait for the first item
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break

                if item is _QUEUE_SENTINEL:
                    queue.task_done()
                    break

                batch = [item]
                batch_start = time.monotonic()

                # Drain up to 5000 items
                while len(batch) < 5000 and self._running and not self._stopping:
                    # First try immediate drain of available items
                    try:
                        next_item = queue.get_nowait()
                        if next_item is _QUEUE_SENTINEL:
                            queue.task_done()
                            self._running = False
                            break
                        batch.append(next_item)
                        continue
                    except asyncio.QueueEmpty:
                        pass

                    # If queue is empty, wait for next item up to remaining debounce window
                    elapsed = time.monotonic() - batch_start
                    remaining = self._debounce_seconds - elapsed
                    if remaining <= 0:
                        break

                    try:
                        next_item = await asyncio.wait_for(queue.get(), timeout=remaining)
                        if next_item is _QUEUE_SENTINEL:
                            queue.task_done()
                            self._running = False
                            break
                        batch.append(next_item)
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        break

                if batch:
                    success = await self._flush_batch(batch, queue)
                    if not success and (not self._running or self._stopping):
                        break
        finally:
            self._running = False
            try:
                async with self._drain_lock:
                    await self._drain_queue(queue)
            finally:
                self._drain_done.set()

    async def _flush_batch(self, batch: list[dict], queue: asyncio.Queue) -> bool:
        """Helper to insert batch with retry backoff and broadcast to SSE."""
        if not batch:
            return True
        inserted = False
        backoff = 0.05 if (not self._running or self._stopping) else 0.5
        shutdown_retries = 0
        max_shutdown_retries = 5

        while True:
            try:
                await asyncio.to_thread(self._insert_batch, batch)
                inserted = True
                break
            except asyncio.CancelledError:
                # If cancelled during flush, attempt a shielded insert so in-flight logs are preserved
                try:
                    await asyncio.shield(asyncio.to_thread(self._insert_batch, batch))
                    inserted = True
                    break
                except Exception:
                    raise
            except Exception as e:
                if not self._running or self._stopping:
                    shutdown_retries += 1
                    if shutdown_retries >= max_shutdown_retries:
                        logger.error(
                            f"Shutdown drain failed to insert batch of {len(batch)} logs after {max_shutdown_retries} attempts: {e}."
                        )
                        break
                    logger.warning(
                        f"Retry {shutdown_retries}/{max_shutdown_retries} during shutdown for batch of {len(batch)} logs: {e}. Retrying in {backoff:.2f}s..."
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2.0, 1.0)
                else:
                    logger.warning(
                        f"Error inserting batch of {len(batch)} logs: {e}. Retrying in {backoff:.1f}s..."
                    )
                    try:
                        await asyncio.sleep(backoff)
                    except asyncio.CancelledError:
                        try:
                            await asyncio.shield(asyncio.to_thread(self._insert_batch, batch))
                            inserted = True
                            break
                        except Exception:
                            raise
                    backoff = min(backoff * 2.0, 10.0)

        if inserted:
            record_ingest(len(batch))
            # Broadcast to SSE subscribers on the event loop thread
            from app.core.sse import sse_manager
            for entry in batch:
                await sse_manager.broadcast(entry)

            # Mark as done
            for _ in batch:
                queue.task_done()
            return True
        else:
            # Mark failed batch items as done so queue is not stuck
            for _ in batch:
                queue.task_done()
            return False

    async def _drain_queue(self, queue: asyncio.Queue) -> None:
        """Drain all remaining items in queue in batches of up to 5000 and commit to SQLite."""
        while not queue.empty():
            batch = []
            while len(batch) < 5000:
                try:
                    item = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if item is _QUEUE_SENTINEL:
                    queue.task_done()
                    continue
                batch.append(item)
            if batch:
                success = await self._flush_batch(batch, queue)
                if not success:
                    # If flush failed after all retries, clean up remaining queue items
                    while not queue.empty():
                        try:
                            rem = queue.get_nowait()
                            queue.task_done()
                        except asyncio.QueueEmpty:
                            break
                    break

    async def stop(self) -> None:
        """
        Signal graceful shutdown, drain all remaining items in _log_queue,
        commit them to SQLite in final batch(es), and exit only once the queue is empty.
        """
        logger.info("QueueConsumer stopping: draining pending logs...")
        self._stopping = True
        self._stop_event.set()
        queue = get_queue()

        try:
            queue.put_nowait(_QUEUE_SENTINEL)
        except asyncio.QueueFull:
            pass

        # Yield control briefly so any scheduled consumer.run() task can start/react
        await asyncio.sleep(0)

        if self._started:
            try:
                await asyncio.shield(self._drain_done.wait())
            except asyncio.CancelledError:
                await self._drain_done.wait()
                raise
        else:
            async with self._drain_lock:
                await self._drain_queue(queue)

        if not queue.empty():
            async with self._drain_lock:
                await self._drain_queue(queue)
        logger.info("QueueConsumer stopped: all pending logs drained and committed.")

    def _insert_batch(self, batch: list[dict]) -> None:
        """Synchronous: insert batch into SQLite in a transaction and assign generated row IDs."""
        query = '''
            INSERT INTO logs (
                timestamp, received_at, source_ip, source_alias,
                app_name, facility, severity, message, raw
            ) VALUES (
                :timestamp, :received_at, :source_ip, :source_alias,
                :app_name, :facility, :severity, :message, :raw
            )
        '''
        
        conn = get_connection(self._db_path)
        try:
            cursor = conn.cursor()
            for entry in batch:
                # Defensive clamp & UTC normalization: ensure timestamp is in canonical UTC
                # and no entry is saved with a timestamp in the future compared to received_at
                try:
                    ts_val = entry.get("timestamp")
                    rec_val = entry.get("received_at")
                    if ts_val:
                        dt_ts = datetime.datetime.fromisoformat(ts_val.replace("Z", "+00:00"))
                        if dt_ts.tzinfo is None:
                            local_tz = datetime.datetime.now().astimezone().tzinfo
                            dt_ts = dt_ts.replace(tzinfo=local_tz).astimezone(datetime.timezone.utc)
                        else:
                            dt_ts = dt_ts.astimezone(datetime.timezone.utc)

                        if rec_val:
                            dt_rec = datetime.datetime.fromisoformat(rec_val.replace("Z", "+00:00"))
                            if dt_rec.tzinfo is None:
                                dt_rec = dt_rec.replace(tzinfo=datetime.timezone.utc)
                            else:
                                dt_rec = dt_rec.astimezone(datetime.timezone.utc)

                            if (dt_ts - dt_rec).total_seconds() > 60:
                                dt_ts = dt_rec

                        entry["timestamp"] = dt_ts.isoformat()
                except Exception:
                    if entry.get("received_at"):
                        entry["timestamp"] = entry["received_at"]
                cursor.execute(query, entry)
                entry["id"] = cursor.lastrowid
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()
