"""FastAPI application for My Anime Manager.

App assembly — middleware, routers, static files, lifecycle — lives here.
``backend.api`` re-exports ``app`` so
``uvicorn backend.api:app`` keeps working.
"""

import asyncio
import logging
import os
from time import perf_counter
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..clients.errors import MissingAPIKeyError
from ..db import torrents as torrent_store
from ..logging.logging_config import configure_logging, new_operation_id, operation_context
from ..services import downloader, initial_rss_poll
from ..services.resource_monitor import worker as resource_worker
from . import state
from .routes_downloader import router as downloader_router
from .routes_history import router as history_router
from .routes_rss import router as rss_router
from .routes_resources import router as resources_router
from .routes_settings import router as settings_router
from .routes_system import router as system_router, _watch_worker
from .routes_torrent import router as torrent_router, recover_torrent_monitors

configure_logging()
logger = logging.getLogger(__name__)

_frontend_dist = Path(__file__).parent.parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Startup/shutdown handling (replaces the deprecated @app.on_event)."""
    # ── Startup ──
    # Mount static assets after all routes are registered (production mode)
    if _frontend_dist.exists() and _frontend_dist.is_dir():
        assets_dir = _frontend_dist / "assets"
        if assets_dir.exists():
            _app.mount(
                "/assets",
                StaticFiles(directory=str(assets_dir)),
                name="frontend-assets",
            )

    # Auto-start directory watcher if WATCH_DIR env var is set
    watch_dir = os.environ.get("WATCH_DIR", "")
    if watch_dir:
        state._watch_task = asyncio.create_task(_watch_worker(watch_dir))

    async def recover_pending_torrents() -> None:
        while True:
            if not torrent_store.list_pending_torrents():
                return
            await recover_torrent_monitors()
            await asyncio.sleep(60)

    recovery_task = asyncio.create_task(recover_pending_torrents())
    resource_worker.start()

    yield

    # ── Shutdown ──
    logger.info("Shutting down background workers...")
    recovery_task.cancel()
    await initial_rss_poll.stop()
    await resource_worker.stop()

    # Cancel watch worker
    if state._watch_task and not state._watch_task.done():
        state._watch_task.cancel()
        logger.info("Watch worker cancelled.")

    # Cancel scan worker
    if state._scan_task and not state._scan_task.done():
        state._scan_task.cancel()
        logger.info("Scan worker cancelled.")

    # Stop RSS downloader
    try:
        await asyncio.wait_for(downloader.stop(), timeout=10)
        logger.info("RSS downloader stopped.")
    except (asyncio.TimeoutError, asyncio.CancelledError, Exception) as e:
        logger.warning("RSS downloader stop: %s", e)

    # Cancel download monitor tasks
    for info_hash, task in list(state._download_tasks.items()):
        if not task.done():
            task.cancel()
            logger.info("Download monitor cancelled: %s", info_hash[:8])
    state._download_tasks.clear()

    logger.info("All workers stopped — safe to restart.")


app = FastAPI(
    title="My Anime Manager",
    description="TMDB + Bangumi 联动工具，为 Jellyfin 生成 NFO 元数据，支持 qBittorrent",
    version=__version__,
    lifespan=lifespan,
)


@app.exception_handler(MissingAPIKeyError)
async def missing_api_key_handler(_request: Request, exc: MissingAPIKeyError):
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.middleware("http")
async def log_request_context(request, call_next):
    operation_id = new_operation_id("http")
    started = perf_counter()
    with operation_context(operation_id):
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("HTTP 请求失败: method=%s path=%s", request.method, request.url.path)
            raise
        response.headers["X-Request-ID"] = operation_id
        logger.info("HTTP 请求完成: method=%s path=%s status=%d duration_ms=%.1f",
                    request.method, request.url.path, response.status_code,
                    (perf_counter() - started) * 1000)
        return response

app.include_router(settings_router)
app.include_router(downloader_router)
app.include_router(torrent_router)
app.include_router(rss_router)
app.include_router(history_router)
app.include_router(resources_router)

# ═══════════════════════════════════════════════════════════════════════
# CORS — allow frontend dev servers
# ═══════════════════════════════════════════════════════════════════════

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)


@app.get("/")
async def root():
    """Health check + status overview, or serve the frontend."""
    # If frontend is built, serve index.html
    index_path = _frontend_dist / "index.html"
    if index_path.exists():
        from fastapi.responses import FileResponse
        return FileResponse(
            str(index_path),
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

    return {
        "service": "My Anime Manager",
        "version": __version__,
        "docs": "/docs",
        "watch": {
            "running": state._watch_status["running"],
            "dir": state._watch_status["dir"],
            "processed": state._watch_status["processed"],
            "failed": state._watch_status["failed"],
        },
    }


# System routes (scan, watch, update, SPA) — MUST be last
app.include_router(system_router)
