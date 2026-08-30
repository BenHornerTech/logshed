"""
Main FastAPI application entry point for Homelab Log Hub.
Configures lifespan events, CORS middleware, background ingestion workers, and API routes.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import ai, aliases, auth, logs, notifications, settings, system
from app.collectors.docker_collector import DockerTailer
from app.collectors.syslog import SyslogServer
from app.core.config import get_db_path, get_docker_host
from app.core.migrations import run_migrations
from app.core.pipeline import KeyedMultilineAssembler, QueueConsumer
from app.core.security import get_or_create_master_key
from app.services.retention import PruneWorker
from app.services.storage_metrics import StorageMetricsWorker

logger = logging.getLogger(__name__)

# Module-level worker references for lifespan management
_queue_consumer: Optional[QueueConsumer] = None
_metrics_worker: Optional[StorageMetricsWorker] = None
_prune_worker: Optional[PruneWorker] = None
_syslog_server: Optional[SyslogServer] = None
_docker_tailer: Optional[DockerTailer] = None
_background_tasks: list[asyncio.Task] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.
    Initializes database schema, master encryption keys, and starts background workers.
    """
    global _queue_consumer, _metrics_worker, _prune_worker, _syslog_server, _docker_tailer, _background_tasks

    db_path = get_db_path()
    logger.info(f"Initializing Homelab Log Hub database at {db_path}...")

    # 1. Run migrations and initialize master key
    await asyncio.to_thread(run_migrations, db_path)
    await asyncio.to_thread(get_or_create_master_key)

    # 2. Start QueueConsumer
    _queue_consumer = QueueConsumer(db_path)
    _background_tasks.append(asyncio.create_task(_queue_consumer.run()))

    # 3. Start StorageMetricsWorker
    _metrics_worker = StorageMetricsWorker(db_path)
    _background_tasks.append(asyncio.create_task(_metrics_worker.run()))

    # 4. Start PruneWorker (runs automated daily retention pruning)
    _prune_worker = PruneWorker(db_path)
    _background_tasks.append(asyncio.create_task(_prune_worker.run()))

    # 5. Start Syslog Server (optional / non-fatal in dev/test)
    try:
        assembler = KeyedMultilineAssembler()
        _syslog_server = SyslogServer(assembler=assembler, db_path=db_path, host="0.0.0.0", port=1514)
        _background_tasks.append(asyncio.create_task(_syslog_server.start()))
        logger.info("SyslogServer listener started on port 1514.")
    except Exception as e:
        logger.warning(f"SyslogServer could not be started: {e}")

    # 6. Start Docker Tailer (optional / non-fatal if Docker socket is not present)
    try:
        docker_assembler = KeyedMultilineAssembler()
        _docker_tailer = DockerTailer(assembler=docker_assembler)
        _background_tasks.append(asyncio.create_task(_docker_tailer.run()))
        logger.info(f"DockerTailer started for {get_docker_host()}.")
    except Exception as e:
        logger.warning(f"DockerTailer could not be started: {e}")

    yield

    # Shutdown sequence
    logger.info("Shutting down background workers...")
    if _docker_tailer:
        await _docker_tailer.stop()
    if _syslog_server:
        await _syslog_server.stop()
    if _prune_worker:
        await _prune_worker.stop()
    if _metrics_worker:
        await _metrics_worker.stop()
    if _queue_consumer:
        await _queue_consumer.stop()

    for task in _background_tasks:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    _background_tasks.clear()
    logger.info("Shutdown complete.")


def create_app() -> FastAPI:
    """Factory creating and configuring the FastAPI application instance."""
    app = FastAPI(
        title="Homelab Log Hub",
        description="Unified syslog and Docker container log aggregator with on-demand AI analysis.",
        version="1.0.0",
        lifespan=lifespan,
    )

    # CORS Middleware allowing credentials for Vite frontend development
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:8080",
            "http://127.0.0.1:8080",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount API routers
    api_router = APIRouter(prefix="/api")
    api_router.include_router(auth.router)
    api_router.include_router(logs.router)
    api_router.include_router(settings.router)
    api_router.include_router(aliases.router)
    api_router.include_router(system.router)
    api_router.include_router(notifications.router)
    api_router.include_router(ai.router)

    app.include_router(api_router)

    # Static files serving with SPA fallback
    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.exists():
        assets_dir = static_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        @app.get("/{full_path:path}")
        async def serve_spa(full_path: str):
            if full_path.startswith("api"):
                raise HTTPException(status_code=404, detail="Not Found")
            file_path = static_dir / full_path
            if file_path.is_file():
                return FileResponse(str(file_path))
            index_path = static_dir / "index.html"
            if index_path.is_file():
                return FileResponse(str(index_path))
            raise HTTPException(status_code=404, detail="Static files not found")

    return app


app = create_app()
