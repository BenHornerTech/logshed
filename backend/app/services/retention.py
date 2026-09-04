"""
Log retention pruning and database cleanup service for LogShed.
"""

import asyncio
import logging
import sqlite3
from pathlib import Path
from typing import Optional

from app.core.migrations import get_connection
from app.services.storage_metrics import record_metrics, prune_old_metrics

logger = logging.getLogger(__name__)

def execute_prune(db_path: str | Path, retention_days: int = 30) -> dict:
    """
    Executes iterative batch pruning, FTS5 index compaction, WAL checkpointing,
    and updates storage metrics.
    
    Returns a dict with:
        deleted_logs: Total number of log rows deleted
        deleted_metrics: Total number of old metric rows deleted
        metrics: Newly recorded storage metrics snapshot
    """
    total_deleted = 0
    db_path_obj = Path(db_path)

    with get_connection(db_path_obj) as conn:
        cursor = conn.cursor()
        
        # 1. Iterative batch deletion of logs older than retention_days
        delete_query = """
            DELETE FROM logs WHERE id IN (
                SELECT id FROM logs 
                WHERE timestamp < datetime('now', '-' || ? || ' days') 
                LIMIT 5000
            )
        """
        while True:
            cursor.execute(delete_query, (retention_days,))
            count = cursor.rowcount
            conn.commit()
            total_deleted += count
            if count == 0:
                break

        # 2. Compact FTS5 virtual table segments
        try:
            cursor.execute("INSERT INTO logs_fts(logs_fts) VALUES('optimize');")
            conn.commit()
        except sqlite3.Error as e:
            logger.warning(f"FTS5 compaction warning: {e}")

        # 3. Checkpoint and truncate WAL
        try:
            cursor.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        except sqlite3.Error as e:
            logger.warning(f"WAL checkpoint warning: {e}")

    # 4. Prune storage metrics older than 30 days
    deleted_metrics = prune_old_metrics(db_path_obj)

    # 5. Take fresh storage metrics snapshot
    metrics = record_metrics(db_path_obj)

    logger.info(
        f"Prune completed: deleted {total_deleted} logs, "
        f"{deleted_metrics} old metrics, retention_days={retention_days}"
    )

    return {
        "status": "ok",
        "deleted_logs": total_deleted,
        "deleted_metrics": deleted_metrics,
        "metrics": metrics,
    }


async def execute_prune_async(db_path: str | Path, retention_days: int = 30) -> dict:
    """Async wrapper executing prune on a thread."""
    return await asyncio.to_thread(execute_prune, db_path, retention_days)


class PruneWorker:
    """
    Background worker that runs daily automated retention pruning.
    Executes once every 24 hours using the configured retention_days from system_settings.
    """
    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path)
        self._running = False
        self._stop_event = asyncio.Event()

    async def run(self) -> None:
        """Main loop: run daily retention pruning with exponential backoff on errors."""
        self._running = True
        logger.info("PruneWorker started.")
        backoff = 5.0

        while self._running:
            try:
                retention_days = await asyncio.to_thread(self._get_retention_days)
                await execute_prune_async(self._db_path, retention_days=retention_days)
                backoff = 5.0
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in PruneWorker daily pruning loop: {e}. Retrying in {backoff:.1f}s...")
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
                    break
                except asyncio.TimeoutError:
                    backoff = min(backoff * 2.0, 3600.0)
                    continue

            # Wait 24 hours (86400 seconds) or until stop signal
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=86400.0)
                break
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    def _get_retention_days(self) -> int:
        """Synchronous query for configured retention_days."""
        try:
            with get_connection(self._db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT value FROM system_settings WHERE key = 'retention_days'")
                row = cursor.fetchone()
                if row and row[0]:
                    try:
                        return int(row[0])
                    except ValueError:
                        pass
        except Exception as e:
            logger.warning(f"Failed to read retention_days from database, defaulting to 30: {e}")
        return 30

    async def stop(self) -> None:
        """Signal graceful shutdown."""
        self._running = False
        self._stop_event.set()
        logger.info("PruneWorker stopping.")
