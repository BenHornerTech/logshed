"""
Storage metrics collection service for LogShed.
"""
import asyncio
import datetime
import logging
import os
import shutil
import sqlite3
from pathlib import Path

from app.core.migrations import get_connection

logger = logging.getLogger(__name__)


def sample_storage_metrics(db_path: str | Path) -> dict:
    """
    Collects a single storage metrics snapshot.
    """
    db_path_obj = Path(db_path)
    
    # Calculate database footprint
    db_size = 0
    for suffix in ["", "-wal", "-shm"]:
        file_path = db_path_obj.with_name(f"{db_path_obj.name}{suffix}")
        try:
            db_size += os.path.getsize(file_path)
        except FileNotFoundError:
            pass

    # Disk usage
    parent_dir = db_path_obj.parent
    try:
        usage = shutil.disk_usage(parent_dir)
        disk_free = usage.free
        disk_total = usage.total
    except FileNotFoundError:
        # Fallback if parent dir somehow doesn't exist
        disk_free = 0
        disk_total = 0

    # Query log count
    total_logs = 0
    try:
        with get_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM logs")
            row = cursor.fetchone()
            if row:
                total_logs = row[0]
    except sqlite3.Error as e:
        logger.error(f"Failed to query log count: {e}")

    return {
        "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "db_size_bytes": db_size,
        "disk_free_bytes": disk_free,
        "disk_total_bytes": disk_total,
        "total_logs_count": total_logs
    }


def record_metrics(db_path: str | Path) -> dict:
    """
    Samples and writes storage metrics to the database.
    """
    metrics = sample_storage_metrics(db_path)
    
    try:
        with get_connection(db_path) as conn:
            conn.execute(
                """
                INSERT INTO storage_metrics 
                (recorded_at, db_size_bytes, disk_free_bytes, disk_total_bytes, total_logs_count)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    metrics["recorded_at"],
                    metrics["db_size_bytes"],
                    metrics["disk_free_bytes"],
                    metrics["disk_total_bytes"],
                    metrics["total_logs_count"]
                )
            )
            conn.commit()
    except sqlite3.Error as e:
        logger.error(f"Failed to record storage metrics: {e}")
        
    return metrics


def prune_old_metrics(db_path: str | Path) -> int:
    """
    Deletes storage metrics older than 30 days.
    """
    try:
        with get_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM storage_metrics WHERE recorded_at < datetime('now', '-30 days')")
            deleted = cursor.rowcount
            conn.commit()
            return deleted
    except sqlite3.Error as e:
        logger.error(f"Failed to prune old metrics: {e}")
        return 0


class StorageMetricsWorker:
    """
    Async background worker that samples storage metrics hourly and on manual trigger.
    """
    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path)
        self._running = False
        self._manual_trigger = asyncio.Event()

    async def run(self) -> None:
        """Main loop: sample hourly, or immediately on manual trigger, with exponential backoff on errors."""
        self._running = True
        logger.info("StorageMetricsWorker started.")
        backoff = 5.0
        
        while self._running:
            try:
                # Record metrics
                await asyncio.to_thread(record_metrics, self._db_path)
                backoff = 5.0
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in StorageMetricsWorker loop: {e}. Retrying in {backoff:.1f}s...")
                try:
                    await asyncio.wait_for(self._manual_trigger.wait(), timeout=backoff)
                    self._manual_trigger.clear()
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2.0, 300.0)
                continue
            
            # Wait for 3600 seconds or manual trigger
            try:
                await asyncio.wait_for(self._manual_trigger.wait(), timeout=3600.0)
                # Manual trigger happened, clear the event and loop immediately
                self._manual_trigger.clear()
            except asyncio.TimeoutError:
                # Hourly timer expired, just continue the loop
                pass
            except asyncio.CancelledError:
                break

    async def stop(self) -> None:
        """Signal graceful shutdown."""
        self._running = False
        self._manual_trigger.set()
        logger.info("StorageMetricsWorker stopping.")

    async def trigger_sample(self) -> dict:
        """Trigger an immediate sample (e.g., after prune). Returns the metrics dict."""
        metrics = await asyncio.to_thread(record_metrics, self._db_path)
        self._manual_trigger.set()
        return metrics
