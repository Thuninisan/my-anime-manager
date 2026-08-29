"""FastAPI server for My Anime Manager.

Endpoints:
    POST /api/torrent/preview       — upload .torrent → return full preview JSON
    POST /api/torrent/confirm       — accept (modified) preview JSON → execute
    POST /scan                      — scan directory in background
    GET  /scan/status               — scan progress
    GET  /watch/status              — watch loop status
    GET  /config                    — read config
    PUT  /config                    — update config

Usage:
    uvicorn my_anime_manager.api:app --host 0.0.0.0 --port 8000
"""

import asyncio
import json as _json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import config
from ..services.batch_service import process_torrent
from ..services import rss as rss_service
from ..services import downloader
from ..services.enrich import _compute_rss_offset
from ..services import tmdb as tmdb_service
from ..services.nfo import images as image_service
from ..clients.qbittorrent import login as qb_login, get_torrents_by_hashes, delete_torrent, add_torrent, resume_torrent, get_torrent_files, set_file_priority
from ..utils.torrent_hash import compute_info_hash
from ..utils.torrent_file_reader import read_torrent_file_list
import bencodepy
from ..clients import bangumi as bgm_client
from ..clients import mikan as mikan_client
from .. import data

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

from .. import __version__
from .models import *
from .routes_settings import router as settings_router
from .routes_system import router as system_router, _scan_worker, _watch_worker
from .routes_downloader import router as downloader_router
from .routes_torrent import router as torrent_router
from .routes_rss import router as rss_router
from .routes_history import router as history_router
from . import state

app = FastAPI(
    title="My Anime Manager",
    description="TMDB + Bangumi 联动工具，为 Jellyfin 生成 NFO 元数据，支持 qBittorrent",
    version=__version__,
)

app.include_router(settings_router)
app.include_router(downloader_router)
app.include_router(torrent_router)
app.include_router(rss_router)
app.include_router(history_router)

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
)

# ═══════════════════════════════════════════════════════════════════════
# Static file serving — only when frontend build exists (production)
# ═══════════════════════════════════════════════════════════════════════

_frontend_dist = Path(__file__).parent.parent.parent / "frontend" / "dist"

# ── Update check state ──


@app.on_event("startup")
async def on_startup():
    """Mount static files after all routes are registered (production mode)."""
    if _frontend_dist.exists() and _frontend_dist.is_dir():
        # Mount static assets at /assets/
        assets_dir = _frontend_dist / "assets"
        if assets_dir.exists():
            app.mount(
                "/assets",
                StaticFiles(directory=str(assets_dir)),
                name="frontend-assets",
            )

    # Auto-start directory watcher if WATCH_DIR env var is set
    watch_dir = os.environ.get("WATCH_DIR", "")
    if watch_dir:
        state._watch_task = asyncio.create_task(_watch_worker(watch_dir))


@app.on_event("shutdown")
async def on_shutdown():
    """Gracefully stop all background workers before shutdown/update."""
    logger.info("Shutting down background workers...")

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
