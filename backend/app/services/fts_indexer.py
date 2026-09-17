"""
Asynchronous FTS5 indexing service for LogShed.

Decouples full-text search indexing from raw log ingestion by running
a background worker that indexes newly inserted rows into logs_fts in bulk batches.
"""

import asyncio
import logging
import sqlite3
from pathlib import Path
from typing import Optional, Union

from app.core.migrations import get_connection

logger = logging.getLogger(__name__)


def index_pending_batch(conn: sqlite3.Connection, batch_size: int = 1000) -> int:
    """
    Index a single batch of unindexed logs into logs_fts atomically.
    Reads last_indexed_id from fts_index_state, inserts up to batch_size rows,
    and updates fts_index_state.last_indexed_id in the same transaction.

    Returns:
        Number of newly indexed rows.
    """
    with conn:
        cursor = conn.cursor()
        cursor.execute("SELECT last_indexed_id FROM fts_index_state WHERE id = 1")
        row = cursor.fetchone()
        if row is None:
            return 0
        last_id = row[0]

        cursor.execute(
            """
            INSERT INTO logs_fts(rowid, app_name, source_alias, message)
            SELECT id, app_name, source_alias, message
            FROM logs
            WHERE id > ?
            ORDER BY id ASC
            LIMIT ?
            RETURNING rowid
            """,
            (last_id, batch_size),
        )
        returned_rows = cursor.fetchall()
        if returned_rows:
            new_last_id = returned_rows[-1][0]
            cursor.execute(
                "UPDATE fts_index_state SET last_indexed_id = ?, updated_at = datetime('now') WHERE id = 1",
                (new_last_id,),
            )
            return len(returned_rows)
        return 0


def index_pending_logs(db_path: Union[str, Path], batch_size: int = 5000) -> int:
    """
    Synchronously index all pending logs from logs table into logs_fts.
    Drains unindexed rows in batches and returns the total number of indexed rows.
    Useful for testing, maintenance, and clean shutdown draining.
    """
    total_indexed = 0
    conn = get_connection(db_path)
    try:
        while True:
            indexed = index_pending_batch(conn, batch_size=batch_size)
            total_indexed += indexed
            if indexed < batch_size:
                break
        return total_indexed
    finally:
        conn.close()


class FTSIndexWorker:
    """
    Supervised background worker that indexes unindexed logs from logs table into logs_fts.
    Woken up immediately by QueueConsumer upon batch commit, with a polling fallback
    to guarantee catch-up latency <= 1000ms.
    """

    def __init__(
        self,
        db_path: Union[str, Path],
        batch_size: int = 1000,
        poll_interval: float = 0.5,
    ):
        self._db_path = Path(db_path)
        self._batch_size = batch_size
        self._poll_interval = poll_interval
        self._running = False
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def notify_new_logs(self) -> None:
        """Signal worker that new logs have been committed to the logs table."""
        if self._loop is not None and self._loop.is_running():
            try:
                self._loop.call_soon_threadsafe(self._wake_event.set)
            except RuntimeError:
                self._wake_event.set()
        else:
            self._wake_event.set()

    async def run(self) -> None:
        """Main supervised worker loop processing unindexed logs."""
        self._loop = asyncio.get_running_loop()
        self._running = True
        self._stop_event.clear()
        self._wake_event.clear()
        logger.info("FTSIndexWorker started.")

        while self._running:
            try:
                indexed_count = await asyncio.to_thread(
                    self._index_pending_chunk, self._batch_size
                )
                if indexed_count >= self._batch_size:
                    # Backlog exists: immediately process next chunk without sleeping
                    continue

                self._wake_event.clear()
                try:
                    await asyncio.wait_for(
                        self._wake_event.wait(), timeout=self._poll_interval
                    )
                except asyncio.TimeoutError:
                    pass
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"FTSIndexWorker error during indexing pass: {e}")
                try:
                    await asyncio.sleep(self._poll_interval)
                except asyncio.CancelledError:
                    break

        logger.info("FTSIndexWorker stopped.")

    def _index_pending_chunk(self, batch_size: int) -> int:
        """Synchronously index one batch chunk using a dedicated connection."""
        conn = get_connection(self._db_path)
        try:
            return index_pending_batch(conn, batch_size=batch_size)
        finally:
            conn.close()

    async def stop(self) -> None:
        """Signal graceful shutdown and drain any pending unindexed logs."""
        self._running = False
        self._stop_event.set()
        self._wake_event.set()
        logger.info("FTSIndexWorker stopping: draining pending logs...")
        try:
            await asyncio.to_thread(index_pending_logs, self._db_path, self._batch_size)
        except Exception as e:
            logger.warning(f"Error draining FTS index on shutdown: {e}")
        logger.info("FTSIndexWorker drain complete.")
