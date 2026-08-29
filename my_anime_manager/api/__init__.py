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


# ── /api/rss/download-history/{bangumi_id}/{sort} ──

@app.delete("/api/rss/download-history/{bangumi_id}/{sort}")
async def delete_episode_history(bangumi_id: int, sort: int):
    """Remove a single episode from download history AND qBittorrent."""
    # Get info_hash before removing the record
    ep = data.get_all_episodes(bangumi_id).get(str(sort))
    info_hash = ep.get("info_hash", "") if ep else ""

    # Delete torrent from qBittorrent (with files)
    if info_hash:
        try:
            qb = await qb_login(
                config.QBITTORRENT_URL,
                config.QBITTORRENT_USERNAME,
                config.QBITTORRENT_PASSWORD,
            )
            await delete_torrent(qb, info_hash, delete_files=True)
            logger.info("deleted torrent from qBittorrent: hash=%s... files=True", info_hash[:12])
        except Exception:
            logger.exception("qBittorrent delete failed for hash=%s...", info_hash[:12])

    ok = data.remove_episode_record(bangumi_id, sort)
    if not ok:
        raise HTTPException(404, "记录不存在")
    return {"ok": True}


@app.post("/api/rss/download-history/{bangumi_id}/{sort}")
async def add_episode_history(bangumi_id: int, sort: int):
    """Manually mark a missing episode as downloaded (source='manual')."""
    data.mark_downloaded(
        bangumi_id, sort,
        rss_url="", guid="", source="manual", pub_date="", info_hash="",
    )
    return {"ok": True}


@app.post("/api/rss/download-history/{bangumi_id}/{sort}/upload")
async def upload_episode_torrent(bangumi_id: int, sort: int, file: UploadFile = File(...)):
    """Upload a .torrent file to manually add a missing episode.

    1. Parse torrent → extract name + info_hash
    2. Determine save path from subscription (same logic as RSS downloader)
    3. Add to qBittorrent (paused)
    4. Record in download_history.json (source='add')
    """
    if not file.filename or not file.filename.lower().endswith(".torrent"):
        raise HTTPException(400, "Only .torrent files are accepted")

    # ── Read subscription ──
    subs = data.list_subscriptions()
    sub = next((s for s in subs if s["bangumi_id"] == bangumi_id), None)
    if not sub:
        raise HTTPException(404, "订阅不存在")
    show_name = sub.get("name", str(bangumi_id))
    series_name = sub.get("series_name") or show_name
    bgm_season = sub.get("bgm", {}).get("season", 1)
    tmdb_id = sub.get("tmdb", {}).get("id", 0)
    tmdb_season = sub.get("tmdb", {}).get("season")
    tvdb_ep_val = sort + sub.get("tvdb", {}).get("ep_offset", 0)
    rss_base = config.RSS_DOWNLOAD_PATH or config.QBITTORRENT_SAVE_PATH
    from my_anime_manager.services.nfo import format_download_path
    template = config.RSS_PATH_TEMPLATE
    rel_path = format_download_path(template, sub, sort=sort, tvdb_episode=tvdb_ep_val).lstrip("/")
    rel_dir = str(Path(rel_path).parent)
    _season_dir = str(Path(rss_base) / rel_dir)
    _show_dir = str(Path(_season_dir).parent)

    # ── Save .torrent to temp file ──
    tmp = tempfile.NamedTemporaryFile(suffix=".torrent", delete=False)
    torrent_name = ""
    info_hash = ""
    try:
        contents = await file.read()
        tmp.write(contents)
        tmp.close()

        # ── Bencode parse → torrent name + info_hash ──
        with open(tmp.name, "rb") as f:
            meta = bencodepy.decode(f.read())
        info = meta[b"info"]
        torrent_name = info[b"name"].decode("utf-8", errors="replace")
        info_hash = compute_info_hash(tmp.name)
        logger.info("parsed torrent: name=%s hash=%s...", torrent_name, info_hash[:12])

        # ── Validate: exactly 1 video file ──
        VIDEO_EXTS = {".mkv", ".mp4", ".mka", ".avi", ".mov", ".ts", ".wmv", ".flv", ".webm"}
        file_list = read_torrent_file_list(tmp.name)
        logger.debug("torrent contains %d files", len(file_list))
        video_files = [
            f for f in file_list
            if Path(f["name"]).suffix.lower() in VIDEO_EXTS
        ]
        if len(video_files) != 1:
            logger.warning("rejected: %d video files (expected 1)", len(video_files))
            raise HTTPException(
                400,
                f"种子中视频文件数量不为1 (found {len(video_files)})，请上传单集种子",
            )

        # ── Add to qBittorrent (paused) ──
        logger.info("adding to qBittorrent: save_path=%s", rss_base)
        try:
            qb = await qb_login(
                config.QBITTORRENT_URL,
                config.QBITTORRENT_USERNAME,
                config.QBITTORRENT_PASSWORD,
            )
            add_hash = await add_torrent(qb, tmp.name, rss_base, torrent_name)
            logger.info("added torrent hash=%s...", add_hash[:12])
        except Exception as e:
            logger.exception("qBittorrent add failed")
            raise HTTPException(500, f"qBittorrent 添加失败: {e}")

        # ── Generate metadata + rename (same flow as RSS downloader) ──
        if tmdb_id:
            logger.info("generating metadata (tmdb_id=%d, season=%d)", tmdb_id, bgm_season)
            try:
                files = await get_torrent_files(qb, add_hash)
                old_path = files[0]["name"] if files else torrent_name
                await downloader.generate_metadata(
                    qb, add_hash, bangumi_id, sort,
                    bangumi_id,
                    tmdb_id, show_name,
                    old_path, torrent_name,
                    bgm_season=bgm_season,
                    tmdb_season=tmdb_season,
                    season_dir=_season_dir,
                    show_dir=_show_dir,
                    series_name=series_name,
                )
                logger.info("metadata generated")
            except Exception as e:
                logger.exception("NFO generation failed")
        else:
            logger.info("skipping metadata (no tmdb_id)")

        # ── Resume download ──
        logger.info("resuming torrent")
        try:
            await resume_torrent(qb, add_hash)
            logger.info("torrent resumed")
        except Exception:
            logger.warning("resume failed (non-fatal)", exc_info=True)

        # ── Record in download history ──
        now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        data.mark_downloaded(
            bangumi_id, sort,
            rss_url="",
            guid=torrent_name,
            source="add",
            pub_date=now,
            info_hash=info_hash,
        )
        logger.info("recorded in history (source=add, guid=%s)", torrent_name[:60])

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("unhandled error in upload")
        raise HTTPException(500, f"上传失败: {e}")

    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    return {"ok": True, "torrent_name": torrent_name, "info_hash": info_hash}


@app.post("/api/rss/download-history/{bangumi_id}/{sort}/replace")
async def replace_episode_torrent(bangumi_id: int, sort: int, file: UploadFile = File(...)):
    """Replace an existing episode with a new .torrent file.

    Deletes the old torrent from qBittorrent (with files), then follows
    the same flow as upload.  Records with source="edit".
    """
    if not file.filename or not file.filename.lower().endswith(".torrent"):
        raise HTTPException(400, "Only .torrent files are accepted")

    # ── Delete old torrent ──
    old_ep = data.get_all_episodes(bangumi_id).get(str(sort))
    if old_ep and old_ep.get("info_hash"):
        try:
            qb = await qb_login(
                config.QBITTORRENT_URL,
                config.QBITTORRENT_USERNAME,
                config.QBITTORRENT_PASSWORD,
            )
            await delete_torrent(qb, old_ep["info_hash"], delete_files=True)
            logger.info("replace: deleted old torrent hash=%s...", old_ep["info_hash"][:12])
        except Exception:
            logger.exception("replace: delete old torrent failed, continuing")

    # ── Read subscription ──
    subs = data.list_subscriptions()
    sub = next((s for s in subs if s["bangumi_id"] == bangumi_id), None)
    if not sub:
        raise HTTPException(404, "订阅不存在")
    show_name = sub.get("name", str(bangumi_id))
    series_name = sub.get("series_name") or show_name
    bgm_season = sub.get("bgm", {}).get("season", 1)
    tmdb_id = sub.get("tmdb", {}).get("id", 0)
    tmdb_season = sub.get("tmdb", {}).get("season")
    tvdb_ep_val = sort + sub.get("tvdb", {}).get("ep_offset", 0)
    rss_base = config.RSS_DOWNLOAD_PATH or config.QBITTORRENT_SAVE_PATH
    from my_anime_manager.services.nfo import format_download_path
    template = config.RSS_PATH_TEMPLATE
    rel_path = format_download_path(template, sub, sort=sort, tvdb_episode=tvdb_ep_val).lstrip("/")
    rel_dir = str(Path(rel_path).parent)
    _season_dir = str(Path(rss_base) / rel_dir)
    _show_dir = str(Path(_season_dir).parent)

    # ── Save .torrent to temp file ──
    tmp = tempfile.NamedTemporaryFile(suffix=".torrent", delete=False)
    torrent_name = ""
    info_hash = ""
    try:
        contents = await file.read()
        tmp.write(contents)
        tmp.close()

        # ── Bencode parse → torrent name + info_hash ──
        with open(tmp.name, "rb") as f:
            meta = bencodepy.decode(f.read())
        info = meta[b"info"]
        torrent_name = info[b"name"].decode("utf-8", errors="replace")
        info_hash = compute_info_hash(tmp.name)
        logger.info("replace: parsed torrent name=%s hash=%s...", torrent_name, info_hash[:12])

        # ── Validate: exactly 1 video file ──
        VIDEO_EXTS = {".mkv", ".mp4", ".mka", ".avi", ".mov", ".ts", ".wmv", ".flv", ".webm"}
        file_list = read_torrent_file_list(tmp.name)
        video_files = [f for f in file_list if Path(f["name"]).suffix.lower() in VIDEO_EXTS]
        if len(video_files) != 1:
            raise HTTPException(400, f"种子中视频文件数量不为1 (found {len(video_files)})")

        # ── Add to qBittorrent (paused) ──
        try:
            qb = await qb_login(
                config.QBITTORRENT_URL,
                config.QBITTORRENT_USERNAME,
                config.QBITTORRENT_PASSWORD,
            )
            add_hash = await add_torrent(qb, tmp.name, rss_base, torrent_name)
            logger.info("replace: added torrent hash=%s...", add_hash[:12])
        except Exception as e:
            raise HTTPException(500, f"qBittorrent 添加失败: {e}")

        # ── Generate metadata + rename ──
        if tmdb_id:
            try:
                files = await get_torrent_files(qb, add_hash)
                old_path = files[0]["name"] if files else torrent_name
                await downloader.generate_metadata(
                    qb, add_hash, bangumi_id, sort,
                    bangumi_id, tmdb_id, show_name,
                    old_path, torrent_name,
                    bgm_season=bgm_season, tmdb_season=tmdb_season,
                    season_dir=_season_dir, show_dir=_show_dir,
                    series_name=series_name,
                )
                logger.info("replace: metadata generated")
            except Exception as e:
                logger.exception("replace: NFO generation failed")

        # ── Resume download ──
        try:
            await resume_torrent(qb, add_hash)
        except Exception:
            pass

        # ── Record in download history (source="edit") ──
        now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        data.mark_downloaded(
            bangumi_id, sort,
            rss_url="",
            guid=torrent_name,
            source="edit",
            pub_date=now,
            info_hash=info_hash,
        )
        logger.info("replace: recorded (source=edit)")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("unhandled error in replace")
        raise HTTPException(500, f"替换失败: {e}")

    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    return {"ok": True, "torrent_name": torrent_name, "info_hash": info_hash}


async def _regen_episode_nfo(bangumi_id: int, sort: int) -> None:
    """Regenerate NFO for one downloaded episode using its stored overrides.

    Everything is derived server-side (subscription, info_hash, per-episode
    TMDB overrides, paths) — callers only need to identify the episode.
    Raises HTTPException(4xx) on missing data, HTTPException(500) on failure.
    """
    subs = data.list_subscriptions()
    sub = next((s for s in subs if s["bangumi_id"] == bangumi_id), None)
    if not sub:
        raise HTTPException(404, "订阅不存在")
    if not sub.get("tmdb", {}).get("id"):
        raise HTTPException(400, "订阅未关联 TMDB ID，无法生成 NFO")
    ep = data.get_all_episodes(bangumi_id).get(str(sort))
    if not ep:
        raise HTTPException(404, "该集的下载记录不存在")
    info_hash = ep.get("info_hash", "")
    if not info_hash:
        raise HTTPException(400, "该集没有关联的种子信息")

    show_name = sub.get("name", str(bangumi_id))
    series_name = sub.get("series_name") or show_name
    bgm_season = sub.get("bgm", {}).get("season", 1)
    tvdb_ep_val = sort + sub.get("tvdb", {}).get("ep_offset", 0)
    rss_base = config.RSS_DOWNLOAD_PATH or config.QBITTORRENT_SAVE_PATH
    from my_anime_manager.services.nfo import format_download_path
    rel_path = format_download_path(
        config.RSS_PATH_TEMPLATE, sub, sort=sort, tvdb_episode=tvdb_ep_val,
    ).lstrip("/")
    rel_dir = str(Path(rel_path).parent)
    season_dir = str(Path(rss_base) / rel_dir)
    show_dir = str(Path(season_dir).parent)

    qb = await qb_login(
        config.QBITTORRENT_URL,
        config.QBITTORRENT_USERNAME,
        config.QBITTORRENT_PASSWORD,
    )
    files = await get_torrent_files(qb, info_hash)
    old_path = files[0]["name"] if files else ep.get("guid", "")
    ok = await downloader.generate_metadata(
        qb, info_hash, bangumi_id, sort,
        bangumi_id, sub["tmdb"]["id"], show_name,
        old_path, ep.get("guid", ""),
        bgm_season=bgm_season,
        tmdb_season=sub.get("tmdb", {}).get("season"),
        season_dir=season_dir, show_dir=show_dir,
        series_name=series_name,
    )
    if not ok:
        raise HTTPException(500, "NFO 生成失败")
    logger.info("regen-nfo: NFO regenerated for bangumi=%d sort=%d", bangumi_id, sort)


@app.patch("/api/rss/download-history/{bangumi_id}/{sort}")
async def update_episode_overrides(
    bangumi_id: int, sort: int,
    fields: dict[str, object] = {},
    regen_nfo: bool = False,
):
    """Set TMDB overrides for an episode and optionally regenerate NFO.

    Body: ``{"tmdb_ep": 13, "tmdb_season": 2}`` — one or both fields.
    Query: ``?regen_nfo=true`` to regenerate NFO after setting overrides.
    """
    tmdb_ep = fields.get("tmdb_ep")
    tmdb_season = fields.get("tmdb_season")
    if tmdb_ep is None and tmdb_season is None:
        raise HTTPException(400, "至少需要提供 tmdb_ep 或 tmdb_season")

    ok = data.set_episode_overrides(
        bangumi_id, sort,
        tmdb_ep=int(tmdb_ep) if tmdb_ep is not None else None,
        tmdb_season=int(tmdb_season) if tmdb_season is not None else None,
    )
    if not ok:
        raise HTTPException(404, "该集的下载记录不存在")

    # ── Optional NFO regeneration (failures are logged, not raised, so the
    #    override write above still returns 200) ──
    if regen_nfo:
        try:
            await _regen_episode_nfo(bangumi_id, sort)
        except Exception:
            logger.exception("overrides+PATCH: NFO regeneration failed")


@app.post("/api/rss/download-history/{bangumi_id}/{sort}/regen-nfo")
async def regen_episode_nfo(bangumi_id: int, sort: int):
    """Regenerate NFO for a single episode using its stored TMDB overrides.

    No request body — all inputs (subscription, info_hash, per-episode
    overrides, paths) are derived server-side.
    """
    try:
        await _regen_episode_nfo(bangumi_id, sort)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("regen-nfo: unhandled error")
        raise HTTPException(500, f"NFO 重新生成失败: {e}")
    return {"ok": True}


# System routes (scan, watch, update, SPA) — MUST be last
app.include_router(system_router)
