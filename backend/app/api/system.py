"""
System health, storage metrics, and retention maintenance API endpoints for LogShed.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_current_user, run_db_query
from app.core.config import get_db_path
from app.core.pipeline import get_dropped_count, get_ingest_rate, get_queue
from app.models import HealthResponse, PruneResponse, StorageMetricItem, StorageOverviewResponse
from app.services.retention import execute_prune_async
from app.services.storage_metrics import sample_storage_metrics

router = APIRouter(tags=["System & Maintenance"])


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """
    Container healthcheck endpoint.
    Verifies SQLite connectivity, in-memory queue depth, dropped log counter, and ingest rate.
    """
    def _ping_db(conn):
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        return cursor.fetchone()[0] == 1

    try:
        db_ok = await run_db_query(_ping_db)
        db_status = "ok" if db_ok else "error"
    except Exception:
        db_status = "error"

    queue = get_queue()
    queue_depth = queue.qsize()
    dropped_count = get_dropped_count()
    ingest_rate = get_ingest_rate()

    overall_status = "ok" if db_status == "ok" else "degraded"

    return HealthResponse(
        status=overall_status,
        db=db_status,
        queue_depth=queue_depth,
        dropped_logs=dropped_count,
        ingest_rate=ingest_rate,
    )


@router.post("/maintenance/prune", response_model=PruneResponse)
async def trigger_prune(user: dict = Depends(get_current_user)) -> PruneResponse:
    """
    Manually triggers log retention pruning, FTS5 index optimization,
    WAL truncation, and takes a fresh storage metrics snapshot.
    """
    def _get_retention_days(conn):
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM system_settings WHERE key = 'retention_days'")
        row = cursor.fetchone()
        if row and row["value"]:
            try:
                return int(row["value"])
            except ValueError:
                pass
        return 30

    retention_days = await run_db_query(_get_retention_days)
    db_path = get_db_path()

    result = await execute_prune_async(db_path, retention_days=retention_days)

    metrics_raw = result["metrics"]
    metric_item = StorageMetricItem(
        recorded_at=str(metrics_raw["recorded_at"]),
        db_size_bytes=metrics_raw["db_size_bytes"],
        disk_free_bytes=metrics_raw["disk_free_bytes"],
        disk_total_bytes=metrics_raw["disk_total_bytes"],
        total_logs_count=metrics_raw["total_logs_count"],
    )

    return PruneResponse(
        status="ok",
        deleted_logs=result["deleted_logs"],
        deleted_metrics=result["deleted_metrics"],
        metrics=metric_item,
    )


@router.get("/system/storage", response_model=StorageOverviewResponse)
async def get_storage_overview(user: dict = Depends(get_current_user)) -> StorageOverviewResponse:
    """
    Fetch current disk usage, DB footprint, log count, and up to 30 days of historical storage metrics.
    """
    db_path = get_db_path()
    current = sample_storage_metrics(db_path)

    def _get_history(conn):
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT recorded_at, db_size_bytes, disk_free_bytes, disk_total_bytes, total_logs_count
            FROM storage_metrics
            WHERE recorded_at >= datetime('now', '-30 days')
            ORDER BY recorded_at ASC
            """
        )
        rows = cursor.fetchall()
        return [
            StorageMetricItem(
                recorded_at=str(r["recorded_at"]),
                db_size_bytes=r["db_size_bytes"],
                disk_free_bytes=r["disk_free_bytes"],
                disk_total_bytes=r["disk_total_bytes"],
                total_logs_count=r["total_logs_count"],
            )
            for r in rows
        ]

    history = await run_db_query(_get_history)

    return StorageOverviewResponse(
        db_size_bytes=current["db_size_bytes"],
        disk_free_bytes=current["disk_free_bytes"],
        disk_total_bytes=current["disk_total_bytes"],
        total_logs_count=current["total_logs_count"],
        history=history,
    )
