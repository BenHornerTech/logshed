"""
Main FastAPI application entry point for LogShed.
Configures lifespan events, CORS middleware, background ingestion workers, and API routes.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import ai, aliases, auth, logs, settings, system
from app.collectors.docker_collector import DockerTailer
from app.collectors.syslog import SyslogServer
from app.core.config import get_cors_origins, get_db_path, get_docker_host
from app.core.migrations import run_migrations
from app.core.pipeline import KeyedMultilineAssembler, QueueConsumer, InternalLogHandler
from app.core.security import get_or_create_master_key
from app.services.retention import PruneWorker
from app.services.storage_metrics import StorageMetricsWorker

logger = logging.getLogger(__name__)

# Silence uvicorn HTTP request access logger to prevent self-logging feedback loops
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

# Module-level worker references for lifespan management
_queue_consumer: Optional[QueueConsumer] = None
_metrics_worker: Optional[StorageMetricsWorker] = None
_prune_worker: Optional[PruneWorker] = None
_syslog_server: Optional[SyslogServer] = None
_docker_tailer: Optional[DockerTailer] = None
_assembler: Optional[KeyedMultilineAssembler] = None
_background_tasks: list[asyncio.Task] = []


async def _supervise_worker(coro_fn, name: str, *args, **kwargs) -> None:
    """
    Supervisor wrapper running a worker coroutine with exception isolation
    and exponential backoff restart without crashing the event loop (SPEC.md §1).
    """
    backoff = 1.0
    while True:
        try:
            await coro_fn(*args, **kwargs)
            break
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Worker '{name}' crashed with error: {e}. Restarting in {backoff:.1f}s...")
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                break
            backoff = min(backoff * 2.0, 60.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.
    Sets up database schema, master encryption keys, and starts background workers.
    """
    global _queue_consumer, _metrics_worker, _prune_worker, _syslog_server, _docker_tailer, _assembler, _background_tasks

    db_path = get_db_path()
    logger.info(f"Setting up LogShed database at {db_path}...")

    # 1. Run migrations and set up master key
    await asyncio.to_thread(run_migrations, db_path)
    await asyncio.to_thread(get_or_create_master_key)

    # 2. Shared KeyedMultilineAssembler for all collectors
    _assembler = KeyedMultilineAssembler()

    # 3. Start QueueConsumer
    _queue_consumer = QueueConsumer(db_path)
    _background_tasks.append(asyncio.create_task(_supervise_worker(_queue_consumer.run, "QueueConsumer")))

    # 4. Start StorageMetricsWorker
    _metrics_worker = StorageMetricsWorker(db_path)
    _background_tasks.append(asyncio.create_task(_supervise_worker(_metrics_worker.run, "StorageMetricsWorker")))

    # 5. Start PruneWorker (runs automated daily retention pruning)
    _prune_worker = PruneWorker(db_path)
    _background_tasks.append(asyncio.create_task(_supervise_worker(_prune_worker.run, "PruneWorker")))

    # 6. Start Syslog Server (optional / non-fatal in dev/test)
    try:
        _syslog_server = SyslogServer(assembler=_assembler, db_path=db_path, host="0.0.0.0", port=1514)
        _background_tasks.append(asyncio.create_task(_supervise_worker(_syslog_server.start, "SyslogServer")))
        logger.info("SyslogServer listener started on port 1514.")
    except Exception as e:
        logger.warning(f"SyslogServer could not be started: {e}")

    # 7. Start Docker Tailer (optional / non-fatal if Docker socket is not present)
    try:
        _docker_tailer = DockerTailer(assembler=_assembler)
        _background_tasks.append(asyncio.create_task(_supervise_worker(_docker_tailer.run, "DockerTailer")))
    except Exception as e:
        logger.warning(f"DockerTailer could not be started: {e}")

    # 8. Attach internal log handler so application warnings and errors appear in LogShed
    internal_handler = InternalLogHandler()
    internal_handler.setLevel(logging.INFO)
    logging.getLogger("app").addHandler(internal_handler)

    yield

    # Shutdown sequence
    logger.info("Shutting down background workers...")
    logging.getLogger("app").removeHandler(internal_handler)
    if _docker_tailer:
        try:
            await _docker_tailer.stop()
        except Exception as e:
            logger.warning(f"Error stopping DockerTailer: {e}")
    if _syslog_server:
        try:
            await _syslog_server.stop()
        except Exception as e:
            logger.warning(f"Error stopping SyslogServer: {e}")
    if _assembler:
        try:
            await _assembler.flush_all()
        except Exception as e:
            logger.warning(f"Error flushing multiline assembler: {e}")
    if _prune_worker:
        try:
            await _prune_worker.stop()
        except Exception as e:
            logger.warning(f"Error stopping PruneWorker: {e}")
    if _metrics_worker:
        try:
            await _metrics_worker.stop()
        except Exception as e:
            logger.warning(f"Error stopping StorageMetricsWorker: {e}")
    if _queue_consumer:
        try:
            await _queue_consumer.stop()
        except Exception as e:
            logger.error(f"Error stopping QueueConsumer: {e}")

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
        title="LogShed",
        description="Unified syslog and Docker container log aggregator with on-demand AI analysis.",
        version="1.0.0",
        lifespan=lifespan,
    )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        # Format clean, human-readable error messages without dumping massive raw input payloads
        messages = []
        for err in exc.errors():
            msg = err.get("msg", "Validation error")
            loc = " -> ".join(str(l) for l in err.get("loc", []) if l != "body")
            messages.append(f"{loc}: {msg}" if loc else msg)
        clean_detail = "; ".join(messages) if messages else "Request validation failed."
        return JSONResponse(
            status_code=422,
            content={"detail": clean_detail},
        )

    # CORS Middleware allowing credentials for Vite frontend development
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_cors_origins(),
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
