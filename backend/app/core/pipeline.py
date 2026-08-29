"""
Core log ingestion pipeline components for Homelab Log Hub.
Provides the shared queue, multiline assembly, and SQLite batch consumer.
"""

import asyncio
import logging
from typing import Optional
from pathlib import Path
from collections import defaultdict

from app.core.migrations import get_connection

logger = logging.getLogger(__name__)

# Module-level shared state
_log_queue: Optional[asyncio.Queue] = None
_dropped_logs_total: int = 0
_QUEUE_MAXSIZE = 10000

def get_queue() -> asyncio.Queue:
    """Returns the singleton log queue, creating it if needed."""
    global _log_queue
    if _log_queue is None:
        _log_queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
    return _log_queue

def get_dropped_count() -> int:
    """Returns the total number of logs dropped due to queue overflow."""
    return _dropped_logs_total

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
        self._lock = asyncio.Lock()
        self._flush_timeout = 0.150  # 150ms

    async def feed(self, stream_key: str, entry: dict) -> None:
        """
        Feed a parsed log entry. Entry dict has keys:
        timestamp, received_at, source_ip, source_alias, app_name,
        facility, severity, message, raw
        """
        message = entry.get('message', '')
        is_cont = _is_continuation(message)
        
        async with self._lock:
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
                self._schedule_flush, 
                stream_key
            )

    def _schedule_flush(self, stream_key: str) -> None:
        """Scheduled by call_later to flush asynchronously."""
        asyncio.create_task(self._flush_stream_locked(stream_key))

    async def _flush_stream_locked(self, stream_key: str) -> None:
        """Locking wrapper around internal flush logic."""
        async with self._lock:
            self._flush_stream_internal(stream_key)

    async def _flush_stream(self, stream_key: str) -> None:
        """Public async flush method for a stream."""
        async with self._lock:
            self._flush_stream_internal(stream_key)

    def _flush_stream_internal(self, stream_key: str) -> None:
        """
        Internal flush logic, must be called with lock held.
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
            global _dropped_logs_total
            _dropped_logs_total += 1

    async def flush_all(self) -> None:
        """Flush all streams. Called on shutdown."""
        async with self._lock:
            keys = list(self._buffers.keys())
            for k in keys:
                self._flush_stream_internal(k)

class QueueConsumer:
    """
    Background task that drains the shared queue and batch-inserts into SQLite.
    """
    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path)
        self._running = False

    async def run(self) -> None:
        """Main loop: drain queue every 2s or 5000 records."""
        self._running = True
        queue = get_queue()
        
        while self._running:
            batch = []
            
            # Collect up to 5000 items
            while len(batch) < 5000:
                try:
                    item = queue.get_nowait()
                    batch.append(item)
                except asyncio.QueueEmpty:
                    break
                    
            if batch:
                try:
                    await asyncio.to_thread(self._insert_batch, batch)
                except Exception as e:
                    logger.error(f"Error inserting batch: {e}")
                    
                # Mark as done
                for _ in batch:
                    queue.task_done()
                    
            if self._running:
                await asyncio.sleep(2.0)

    async def stop(self) -> None:
        """Signal graceful shutdown."""
        self._running = False

    def _insert_batch(self, batch: list[dict]) -> None:
        """Synchronous: insert batch into SQLite in a transaction and notify SSE."""
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
            cursor.executemany(query, batch)
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

        # Broadcast newly inserted items to active SSE subscribers
        from app.core.sse import sse_manager
        for entry in batch:
            sse_manager.broadcast_sync(entry)
