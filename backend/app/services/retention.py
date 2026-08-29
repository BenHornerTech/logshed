"""
Log retention pruning and database optimization service for Homelab Log Hub.
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
    Executes iterative batch pruning, FTS5 optimization, WAL checkpointing,
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

        # 2. Optimize FTS5 virtual table
        try:
            cursor.execute("INSERT INTO logs_fts(logs_fts) VALUES('optimize');")
            conn.commit()
        except sqlite3.Error as e:
            logger.warning(f"FTS5 optimize warning: {e}")

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
