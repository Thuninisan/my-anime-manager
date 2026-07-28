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
from . import state

app = FastAPI(
    title="My Anime Manager",
    description="TMDB + Bangumi 联动工具，为 Jellyfin 生成 NFO 元数据，支持 qBittorrent",
    version=__version__,
)

app.include_router(settings_router)
app.include_router(downloader_router)

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


# ── /api/torrent/subtitle/upload ──

# Allowed subtitle file extensions
_ALLOWED_SUB_EXTENSIONS: set[str] = {".ass", ".ssa", ".srt", ".sub", ".idx", ".vtt", ".ttml", ".sbv", ".dfxp"}

# Subtitle storage root (under the data directory)
_SUBTITLE_DIR = Path(__file__).parent / "data" / "subtitles"


@app.post("/api/torrent/subtitle/upload")
async def subtitle_upload(
    file: UploadFile = File(...),
    torrent_name: str = Form(...),
    target_stem: str = Form(""),
):
    """Upload a subtitle file for a specific torrent.

    The file is stored under ``data/subtitles/{torrent_name}/`` so it can be
    copied alongside the media files during the confirm phase.

    If *target_stem* is provided the file is renamed to ``{target_stem}{ext}``
    so the frontend can match it to a specific video file by filename stem
    (used by batch folder upload).

    Only common subtitle formats are accepted (.ass, .srt, etc.).
    """
    if not file.filename:
        raise HTTPException(400, "未提供文件名")

    ext = Path(file.filename).suffix.lower()
    if ext not in _ALLOWED_SUB_EXTENSIONS:
        raise HTTPException(
            400,
            f"不支持的字幕格式: {ext}。支持的格式: {', '.join(sorted(_ALLOWED_SUB_EXTENSIONS))}",
        )

    # Sanitise torrent_name for use as directory name
    safe_torrent_name = re.sub(r'[<>:"/\\|?*]', "_", torrent_name).strip()
    if not safe_torrent_name:
        raise HTTPException(400, "种子名称为空")

    dest_dir = _SUBTITLE_DIR / safe_torrent_name
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Determine the stored filename: use target_stem if provided, else original name
    if target_stem:
        safe_stem = re.sub(r'[<>:"/\\|?*]', "_", target_stem).strip()
        if not safe_stem:
            raise HTTPException(400, "target_stem 无效")
        dest_filename = f"{safe_stem}{ext}"
    else:
        dest_filename = file.filename

    # Avoid overwriting — append a counter if the file already exists
    dest_path = dest_dir / dest_filename
    if dest_path.exists():
        stem, suffix = dest_path.stem, dest_path.suffix
        counter = 1
        while dest_path.exists():
            dest_path = dest_dir / f"{stem}_{counter}{suffix}"
            counter += 1

    content = await file.read()
    dest_path.write_bytes(content)

    logger.info("字幕上传成功: %s → %s", file.filename, dest_path)

    return {
        "ok": True,
        "filename": dest_path.name,
        "original_filename": file.filename,
        "torrent_name": safe_torrent_name,
        "stored_path": str(dest_path),
    }


@app.delete("/api/torrent/subtitle/delete")
async def subtitle_delete(torrent_name: str, filename: str):
    """Delete a user-uploaded subtitle file.

    Only removes files under ``data/subtitles/{torrent_name}/`` — the endpoint
    rejects paths that attempt directory traversal.
    """
    # Sanitise inputs to prevent directory traversal
    safe_torrent_name = re.sub(r'[<>:"/\\|?*]', "_", torrent_name).strip()
    safe_filename = Path(filename).name  # strip any directory components

    if not safe_torrent_name or not safe_filename:
        raise HTTPException(400, "种子名称或文件名为空")

    file_path = _SUBTITLE_DIR / safe_torrent_name / safe_filename

    # Resolve and verify the path stays within the subtitles directory
    try:
        file_path = file_path.resolve()
        _SUBTITLE_DIR.resolve()
        if not str(file_path).startswith(str(_SUBTITLE_DIR.resolve()) + os.sep):
            raise HTTPException(403, "路径越界")
    except (ValueError, OSError):
        raise HTTPException(400, "无效的文件路径")

    if not file_path.is_file():
        raise HTTPException(404, f"字幕文件不存在: {safe_filename}")

    file_path.unlink()
    logger.info("字幕已删除: %s", file_path)

    # Clean up empty parent directory
    parent = file_path.parent
    if parent != _SUBTITLE_DIR and not any(parent.iterdir()):
        parent.rmdir()

    return {"ok": True, "deleted": safe_filename}


# ── /api/torrent/parse-and-search ──

@app.post("/api/torrent/parse-and-search")
async def torrent_parse_and_search(file: UploadFile = File(...)):
    """Parse a .torrent file and search TMDB + Bangumi for matched shows.

    Independent endpoint — does NOT use the existing build_preview flow.
    Upload a .torrent, get back parsed file list + deduplicated show names
    + parallel TMDB/Bangumi search results.

    Returns:
        JSON with torrent_name, parsed_files, skipped_files, show_names,
        and search_results (tmdb / bangumi each with default + backup).
    """
    if not file.filename or not file.filename.endswith(".torrent"):
        raise HTTPException(400, "请上传 .torrent 文件")

    # Save uploaded file to temp location
    with tempfile.NamedTemporaryFile(suffix=".torrent", delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        from ..services.torrent_preview import parse_and_search
        result = await parse_and_search(tmp_path)
    except Exception as e:
        Path(tmp_path).unlink(missing_ok=True)
        traceback.print_exc()
        raise HTTPException(400, str(e))

    # Keep the temp file — the download endpoint needs it later
    return result


# ── /api/torrent/bangumi/{id}/episodes ──

@app.get("/api/torrent/bangumi/{bangumi_id}/episodes")
async def torrent_bangumi_episodes(bangumi_id: int):
    """Fetch episode data for a Bangumi subject (main + SP).

    Used by the frontend to add extra Bangumi entries to the match table.
    """
    try:
        eps_main = await bgm_client.get_episodes(bangumi_id, ep_type=0)
    except Exception:
        eps_main = []
    try:
        eps_sp = await bgm_client.get_episodes(bangumi_id, ep_type=1)
    except Exception:
        eps_sp = []

    try:
        subject = await bgm_client.get_subject(bangumi_id)
        name = subject.get("name_cn") or subject.get("name", str(bangumi_id))
    except Exception:
        name = str(bangumi_id)

    all_eps = (eps_main or []) + (eps_sp or [])
    clean_eps = []
    for ep in all_eps:
        entry = {
            "sort": ep.get("sort") or ep.get("ep", 0),
            "id": ep["id"],
            "name": ep.get("name", ""),
        }
        cn = ep.get("name_cn")
        if cn and cn != entry["name"]:
            entry["name_cn"] = cn
        clean_eps.append(entry)
    clean_eps.sort(key=lambda x: x["sort"])

    return {
        "id": bangumi_id,
        "name": name,
        "episodes": clean_eps,
    }


# ── /api/torrent/download ──


def _sanitize_path_component(name: str) -> str:
    """Remove characters that are illegal in directory / file names."""
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip()


def _make_sub_for_path(f: dict, series_name: str = "") -> dict:
    """Build a pseudo-subscription dict for :func:`format_download_path`."""
    bgm_name = f.get("bangumi_show_name", "")
    return {
        "name": bgm_name,
        "series_name": series_name or bgm_name,
        "bgm": {
            "subject_name": bgm_name,
            "season": 1,
        },
        "tvdb": {
            "season": f.get("tvdb_season") or f.get("tmdb_season", 1),
        },
        "tmdb": {
            "season": f.get("tmdb_season", 1),
        },
    }


def _find_tmdb_id(preview_data: dict | None, show_name: str) -> int:
    """Find TMDB ID from preview_data for a given show name."""
    if not preview_data:
        return 0
    search_results = preview_data.get("search_results", {})
    for entry in search_results.values():
        tmdb = entry.get("tmdb", {}) if isinstance(entry, dict) else {}
        if tmdb.get("id"):
            return tmdb["id"]
    return 0


def _find_tvdb_id(preview_data: dict | None, bgm_id: int) -> int:
    """Find TVDB ID from preview_data's map_entries for a given BGM ID."""
    if not preview_data or not bgm_id:
        return 0
    search_results = preview_data.get("search_results", {})
    for entry in search_results.values():
        map_entries = entry.get("map_entries", []) if isinstance(entry, dict) else []
        for me in map_entries:
            if me.get("bangumi_id") == bgm_id and me.get("tvdb_id"):
                return me["tvdb_id"]
    return 0


def _derive_series_name(preview_data: dict | None) -> str:
    """Derive the root series name for path template ``{series_name}``.

    Priority: TMDB name from ``search_results`` → BGM name from
    ``episode_data.bangumi``.  This mirrors the RSS enrichment flow
    which prefers TMDB zh-CN over BGM.
    """
    if not preview_data:
        return ""

    # 1. Try TMDB name from search_results (usually Chinese or best
    #    available localised title)
    search_results = preview_data.get("search_results", {})
    for entry in search_results.values():
        if isinstance(entry, dict):
            tmdb = entry.get("tmdb")
            if isinstance(tmdb, dict) and tmdb.get("name"):
                return tmdb["name"]

    # 2. Fallback: first BGM entry from episode_data
    episode_data = preview_data.get("episode_data", {})
    bgm_data: dict = episode_data.get("bangumi", {})
    if bgm_data:
        first = next(iter(bgm_data.values()))
        if isinstance(first, dict):
            return first.get("name", "")

    return ""


async def _monitor_download(
    info_hash: str,
    torrent_name: str,
    files: list[dict],
    uploaded_subtitles: list[dict],
    hardlink_root: str,
    series_name: str = "",
    *,
    skip_nfo: bool = False,
    movie_meta: dict | None = None,
):
    """Background task: poll qBittorrent until download completes, then
    create hardlinks / copy subtitles.

    When *skip_nfo* is True the inline NFO generation is skipped (it was
    already done before the torrent was resumed).
    """
    subtitle_dir = _SUBTITLE_DIR / _sanitize_path_component(torrent_name)

    # Login for the background task
    try:
        client = await qb_login(
            config.QBITTORRENT_URL,
            config.QBITTORRENT_USERNAME,
            config.QBITTORRENT_PASSWORD,
        )
    except Exception as e:
        logger.error("下载监控登录失败 [%s]: %s", torrent_name, e)
        return

    import time
    deadline = time.monotonic() + 86400  # 24h max
    while time.monotonic() < deadline:
        await asyncio.sleep(5)
        try:
            torrents = await get_torrents_by_hashes(client, [info_hash])
        except Exception as e:
            logger.warning("下载监控轮询失败 [%s]: %s", torrent_name, e)
            continue

        t = torrents.get(info_hash)
        if not t:
            continue

        progress = t.get("progress", 0)
        state = t.get("state", "")

        if progress >= 1.0 or "paused" in state.lower() or "stopped" in state.lower() or "completed" in state.lower():
            logger.info("下载完成 [%s] (%.1f%%)", torrent_name, progress * 100)
            if progress < 1.0:
                logger.warning("种子状态异常 (progress=%.2f, state=%s), 仍然尝试创建文件", progress, state)

            save_path = t.get("save_path", hardlink_root)
            logger.info("下载完成 [%s], 开始创建硬链接/复制字幕...", torrent_name)

            created = 0

            if movie_meta:
                # ── Movie mode: flat structure {hardlink_root}/{tmdb_name}/{tmdb_name}.ext ──
                tmdb_name = movie_meta["tmdb_name"]
                movie_dir = Path(hardlink_root) / tmdb_name
                movie_dir.mkdir(parents=True, exist_ok=True)

                for f in files:
                    torrent_path = f["torrent_path"]
                    is_sub = f.get("is_subtitle", False)
                    src_ext = Path(torrent_path).suffix
                    src_path = Path(save_path) / torrent_path

                    if is_sub:
                        dest_path = movie_dir / f"{tmdb_name}{src_ext}"
                    else:
                        dest_path = movie_dir / f"{tmdb_name}{src_ext}"

                    try:
                        if src_path.exists():
                            if is_sub:
                                shutil.copy2(src_path, dest_path)
                            else:
                                if dest_path.exists():
                                    dest_path.unlink()
                                os.link(src_path, dest_path)
                            created += 1
                            logger.info("   %s → %s [%s]", src_path.name, dest_path, "copy" if is_sub else "hardlink")
                        else:
                            logger.warning("   源文件不存在: %s", src_path)
                    except OSError as e:
                        logger.error("   创建文件失败: %s → %s — %s", src_path, dest_path, e)

                # Copy user-uploaded subtitles
                for usub in uploaded_subtitles:
                    stored_name = usub.get("stored_filename", "")
                    src_sub = subtitle_dir / stored_name
                    if not src_sub.exists():
                        logger.warning("   上传的字幕文件不存在: %s", src_sub)
                        continue
                    dest_path = movie_dir / f"{tmdb_name}{src_sub.suffix}"
                    try:
                        shutil.copy2(src_sub, dest_path)
                        created += 1
                        logger.info("   [uploaded] %s → %s", stored_name, dest_path)
                    except OSError as e:
                        logger.error("   复制上传字幕失败: %s → %s — %s", src_sub, dest_path, e)

            else:
                # ── TV mode: path template ──
                template = config.RSS_PATH_TEMPLATE
                from ..services.nfo import format_download_path
                from ..services.nfo import (
                    write_episode_files,
                    generate_tv_show_nfo,
                    generate_season_nfo,
                )

                seen_show_dirs: set[str] = set()
                seen_season_dirs: set[str] = set()

                for f in files:
                    torrent_path = f["torrent_path"]
                    is_sub = f.get("is_subtitle", False)
                    src_ext = Path(torrent_path).suffix

                    sub = _make_sub_for_path(f, series_name)
                    tvdb_ep = f.get("tvdb_episode") or 0
                    tmdb_ep = f.get("tmdb_episode") or 0

                    rel_path = format_download_path(
                        template, sub,
                        tvdb_episode=tvdb_ep, tmdb_episode=tmdb_ep,
                    ).lstrip("/")
                    # Replace extension with the actual source extension
                    rel_path = str(Path(rel_path).with_suffix(src_ext))

                    dest_path = Path(hardlink_root) / rel_path
                    dest_path.parent.mkdir(parents=True, exist_ok=True)

                    # Source: qBittorrent save_path / torrent_path
                    src_path = Path(save_path) / torrent_path

                    try:
                        if src_path.exists():
                            if is_sub:
                                shutil.copy2(src_path, dest_path)
                            else:
                                if dest_path.exists():
                                    dest_path.unlink()
                                os.link(src_path, dest_path)
                            created += 1
                            logger.info("   %s → %s [%s]", src_path.name, dest_path, "copy" if is_sub else "hardlink")
                        else:
                            logger.warning("   源文件不存在: %s", src_path)
                    except OSError as e:
                        logger.error("   创建文件失败: %s → %s — %s", src_path, dest_path, e)

                # Copy user-uploaded subtitles
                for usub in uploaded_subtitles:
                    stored_name = usub.get("stored_filename", "")
                    src_sub = subtitle_dir / stored_name
                    if not src_sub.exists():
                        logger.warning("   上传的字幕文件不存在: %s", src_sub)
                        continue

                    sub = _make_sub_for_path(usub, series_name)
                    tvdb_ep = usub.get("tvdb_episode") or 0
                    tmdb_ep = usub.get("tmdb_episode") or 0

                    rel_path = format_download_path(
                        template, sub,
                        tvdb_episode=tvdb_ep, tmdb_episode=tmdb_ep,
                    ).lstrip("/")
                    rel_path = str(Path(rel_path).with_suffix(src_sub.suffix))

                    dest_path = Path(hardlink_root) / rel_path
                    dest_path.parent.mkdir(parents=True, exist_ok=True)

                    try:
                        shutil.copy2(src_sub, dest_path)
                        created += 1
                        logger.info("   [uploaded] %s → %s", stored_name, dest_path)
                    except OSError as e:
                        logger.error("   复制上传字幕失败: %s → %s — %s", src_sub, dest_path, e)

            # ── Generate NFO files (skipped if already done pre-resume) ──
            # Movie: always skip (pre-generated or skipped entirely)
            # TV: generate inline if skip_nfo is False
            nfo_generated = 0
            if movie_meta:
                logger.info("电影 NFO 已预生成，跳过内联 NFO 生成 [%s]", torrent_name)
            elif skip_nfo:
                logger.info("NFO 已预生成，跳过内联 NFO 生成 [%s]", torrent_name)
            else:
                for f in files:
                    is_sub = f.get("is_subtitle", False)
                    if is_sub:
                        continue

                    sub = _make_sub_for_path(f, series_name)
                    tvdb_ep = f.get("tvdb_episode") or 0
                    tmdb_ep = f.get("tmdb_episode") or 0

                    # Compute paths via template
                    rel_path = format_download_path(
                        template, sub,
                        tvdb_episode=tvdb_ep, tmdb_episode=tmdb_ep,
                    ).lstrip("/")
                    file_stem = Path(rel_path).stem
                    season_dir = Path(hardlink_root) / Path(rel_path).parent
                    show_dir = season_dir.parent
                    season_dir.mkdir(parents=True, exist_ok=True)

                    # tvshow.nfo (once per show_dir)
                    show_key = str(show_dir)
                    if show_key not in seen_show_dirs:
                        seen_show_dirs.add(show_key)
                        generate_tv_show_nfo(
                            title=f.get("tmdb_show_name", ""),
                            original_title=f.get("bangumi_show_name", ""),
                            plot="",
                            output_dir=str(show_dir),
                        )
                        nfo_generated += 1
                        logger.info("   tvshow.nfo → %s", show_dir / "tvshow.nfo")

                    # season.nfo (once per season_dir)
                    season_key = str(season_dir)
                    if season_key not in seen_season_dirs:
                        seen_season_dirs.add(season_key)
                        bgm_id = f.get("bangumi_id", 0)
                        tmdb_season = f.get("tmdb_season", 0)
                        generate_season_nfo(
                            title=f"Season {tmdb_season}",
                            original_title="",
                            plot="",
                            premiered="",
                            season_number=tmdb_season,
                            bangumi_id=bgm_id,
                            output_dir=str(season_dir),
                        )
                        nfo_generated += 1
                        logger.info("   season.nfo → %s", season_dir / "season.nfo")

                    # Episode NFO
                    await write_episode_files(
                        {},  # tmdb_ep (empty = skip thumb download)
                        season_number=f.get("tmdb_season", 0),
                        episode_number=f.get("tmdb_episode", 0),
                        bangumi_ep_id=f.get("bangumi_ep_id"),
                        show_name=f.get("tmdb_show_name", ""),
                        original_name=f.get("bangumi_show_name", ""),
                        bangumi_subject_name=f.get("bangumi_show_name", ""),
                        studios=[],
                        rating=0,
                        output_dir=str(season_dir),
                        thumb_source="tmdb",
                        file_stem=file_stem,
                    )
                    nfo_generated += 1
                    logger.info("   episode.nfo → %s", season_dir / f"{file_stem}.nfo")

            logger.info("下载后处理完成 [%s]: 创建了 %d 个文件, 生成了 %d 个 NFO", torrent_name, created, nfo_generated)

            # Remove task from tracker
            state._download_tasks.pop(info_hash, None)
            return

    logger.warning("下载监控超时 [%s] (24h)", torrent_name)
    state._download_tasks.pop(info_hash, None)


async def _build_metadata_from_preview(
    preview_data: dict,
    files: list[dict],
) -> tuple[dict, dict, dict] | None:
    """Convert frontend preview data into the format expected by
    :func:`batch_service.generate_metadata_collection`.

    Returns ``(tvshow, seasons, episodes)`` or ``None`` if the data is
    insufficient.
    """
    search_results: dict = preview_data.get("search_results", {})
    episode_data: dict = preview_data.get("episode_data", {})
    if not search_results or not episode_data:
        return None

    # ── tvshow ──
    # Grab the first TMDB result with enough data
    tvshow: dict = {}
    for sr in search_results.values():
        tmdb = sr.get("tmdb", {})
        if tmdb.get("id") and tmdb.get("name"):
            tvshow = {
                "title": tmdb["name"],
                "original_title": tmdb.get("original_name") or tmdb["name"],
                "tmdb_id": tmdb["id"],
                "plot": tmdb.get("overview", ""),
                "premiered": tmdb.get("first_air_date", ""),
                "genres": tmdb.get("genres", []),
                "studios": tmdb.get("studios", []) or tmdb.get("networks", []),
                "rating": tmdb.get("vote_average", 0),
                "status": tmdb.get("status", ""),
            }
            break

    if not tvshow:
        return None

    # ── seasons ──
    # Build season entries from bangumi data
    seasons: dict[str, dict] = {}
    bgm_data: dict = episode_data.get("bangumi", {})
    season_idx = 0
    for bgm_id_str, bgm_info in bgm_data.items():
        if not isinstance(bgm_info, dict):
            continue
        season_idx += 1
        seasons[str(season_idx)] = {
            "bgm_id": int(bgm_id_str),
            "bgm_title": bgm_info.get("name", ""),
            "bgm_original": bgm_info.get("name", ""),
            "bgm_plot": bgm_info.get("summary", ""),
            "bgm_premiered": bgm_info.get("date", ""),
            "bgm_images": bgm_info.get("images"),
        }

    # ── episodes ──
    # Map each matched file to its TMDB episode details.
    # IMPORTANT: the preview data uses camelCase keys (from JSON
    # serialization), but generate_metadata_collection expects
    # snake_case.  We normalise here.
    episodes: dict[str, dict] = {}
    tmdb_data: dict = episode_data.get("tmdb", {})
    for tmdb_id_str, season_map in tmdb_data.items():
        if not isinstance(season_map, dict):
            continue
        for season_str, season_info in season_map.items():
            if not isinstance(season_info, dict):
                continue
            for ep in season_info.get("episodes", []):
                ep_num = ep.get("epNum", 0)
                ep_key = f"S{season_str}E{str(ep_num).zfill(2)}"
                # Normalise camelCase → snake_case for downstream consumers
                episodes[ep_key] = {
                    "season_number": int(season_str),
                    "episode_number": ep_num,
                    "bangumi_ep_id": None,
                    "bangumi_subject_name": "",
                    "tmdb": {
                        "id": ep.get("tmdbId"),
                        "name": ep.get("name", ""),
                        "overview": ep.get("overview", ""),
                        "air_date": ep.get("airDate", ""),
                        "runtime": ep.get("runtime", 0),
                        "still_path": ep.get("stillPath", ""),
                        "vote_average": ep.get("voteAverage", 0) or 0,
                        "directors": ep.get("directors", []),
                        "writers": ep.get("writers", []),
                        "guest_stars": ep.get("guestStars", []),
                    },
                }

    # ── Enrich episodes with Bangumi metadata from the files array ──
    # The frontend MatchTable already matched each file to a Bangumi
    # episode — we just need to cross-reference by (season, episode).
    target_keys: set[str] = set()
    target_seasons: set[int] = set()
    for f in files:
        if f.get("is_subtitle"):
            continue
        sn = f.get("tmdb_season", 0)
        en = f.get("tmdb_episode", 0)
        if sn and en:
            key = f"S{sn}E{str(en).zfill(2)}"
            target_keys.add(key)
            target_seasons.add(sn)
            if key in episodes:
                episodes[key]["bangumi_ep_id"] = f.get("bangumi_ep_id")
                episodes[key]["bangumi_subject_name"] = f.get(
                    "bangumi_show_name", ""
                )

    # Only keep episodes and seasons that are actually being downloaded
    episodes = {k: v for k, v in episodes.items() if k in target_keys}
    seasons = {
        str(sn): sd
        for sn_str, sd in seasons.items()
        if (sn := int(sn_str)) in target_seasons
    }

    # ── Fetch zh-CN credits + Chinese episode data for NFO ──
    # These API calls run AFTER the user confirms download, not during
    # parse-and-search, saving API quota for unconfirmed previews.
    tmdb_id = tvshow.get("tmdb_id", 0)
    if tmdb_id and target_seasons:
        from ..clients.tmdb import (
            get_season_credits,
            get_season_detail,
        )

        for sn in sorted(target_seasons):
            # Fetch season credits with zh-CN for Chinese actor names
            try:
                cred = await get_season_credits(tmdb_id, sn, language="zh-CN")
                cast_list = cred.json().get("cast", [])
                if cast_list:
                    cast = [
                        {"name": c["name"],
                         "character": c.get("character", "")}
                        for c in cast_list
                    ]
                    # Attach to every downloaded episode in this season
                    for ep in episodes.values():
                        if ep["season_number"] == sn:
                            ep["tmdb"]["guest_stars"] = cast
            except Exception:
                pass  # non-fatal

            # Fetch season detail with zh-CN for Chinese episode plots
            try:
                detail = await get_season_detail(
                    tmdb_id, sn, language="zh-CN",
                )
                zh_season = detail.json()
                for zh_ep in zh_season.get("episodes", []):
                    zh_ep_num = zh_ep.get("episode_number", 0)
                    key = f"S{sn}E{str(zh_ep_num).zfill(2)}"
                    if key in episodes:
                        ep_data = episodes[key]["tmdb"]
                        # Save the Japanese original name before overwriting
                        ep_data["original_name"] = ep_data.get("name", "")
                        # Replace overview with Chinese version
                        if zh_ep.get("overview"):
                            ep_data["overview"] = zh_ep["overview"]
                        # Update name to Chinese for NFO title
                        if zh_ep.get("name"):
                            ep_data["name"] = zh_ep["name"]
            except Exception:
                pass  # non-fatal; keep Japanese fallback

    return tvshow, seasons, episodes


@app.post("/api/torrent/download")
async def torrent_download(body: dict):
    """Add a torrent to qBittorrent with selective file download.

    Only the files listed in *files* (and their matching subtitles) are
    downloaded.  After the download completes a background task creates
    hardlinks for video files and copies subtitle files into the configured
    ``TORRENT_HARDLINK_PATH`` directory.

    If *preview_data* is present (the full parse-and-search result), NFO
    files and images are generated **before** the torrent is resumed,
    matching the batch/scan flow behaviour.
    """
    torrent_path = body.get("torrent_path", "")
    torrent_name = body.get("torrent_name", "")
    files: list[dict] = body.get("files", [])
    uploaded_subtitles: list[dict] = body.get("uploaded_subtitles", [])
    preview_data: dict | None = body.get("preview_data")

    if not torrent_path or not Path(torrent_path).is_file():
        raise HTTPException(400, "种子文件不存在")
    if not files:
        raise HTTPException(400, "文件列表为空")

    download_path = config.TORRENT_DOWNLOAD_PATH  # qBittorrent 下载暂存目录
    hardlink_root = config.TORRENT_HARDLINK_PATH   # 下载完成后硬链接目标目录

    # ── Read the full file list from the torrent ──
    try:
        full_file_list = read_torrent_file_list(torrent_path)
    except Exception as e:
        raise HTTPException(400, f"无法读取种子文件: {e}")

    # Build a set of torrent paths that should be downloaded
    download_set: set[str] = {f["torrent_path"] for f in files}

    # ── Login to qBittorrent ──
    try:
        client = await qb_login(
            config.QBITTORRENT_URL,
            config.QBITTORRENT_USERNAME,
            config.QBITTORRENT_PASSWORD,
        )
    except Exception as e:
        raise HTTPException(500, f"qBittorrent 连接失败: {e}")

    # ── Add torrent (paused) ──
    try:
        info_hash = await add_torrent(client, torrent_path, download_path, torrent_name)
        logger.info("种子已添加 [%s]: hash=%s", torrent_name, info_hash[:12])
    except Exception as e:
        raise HTTPException(500, f"添加种子失败: {e}")

    # ── Set file priorities: 1 for files we want, 0 for the rest ──
    try:
        # Get file list from qBittorrent to map paths → indices
        qb_files = await get_torrent_files(client, info_hash)
        skip_indices: list[int] = []
        download_indices: list[int] = []
        for idx, f in enumerate(qb_files):
            fname = f.get("name", "")
            if fname in download_set:
                download_indices.append(idx)
            else:
                skip_indices.append(idx)

        if skip_indices:
            await set_file_priority(client, info_hash, skip_indices, 0)
            logger.info("跳过 %d 个文件", len(skip_indices))

        if download_indices:
            await set_file_priority(client, info_hash, download_indices, 1)
            logger.info("下载 %d 个文件", len(download_indices))
    except Exception as e:
        logger.warning("设置文件优先级失败 (将继续下载所有文件): %s", e)

    # ── Derive series name for path template ──
    series_name = _derive_series_name(preview_data)

    # ── Movie detection: check preview_data for movie content ──
    is_movie = False
    movie_meta: dict | None = None
    if preview_data:
        search_results = preview_data.get("search_results", {})
        for entry in search_results.values():
            if isinstance(entry, dict) and entry.get("media_type") == "movie":
                is_movie = True
                break

    # ── Generate NFO + images BEFORE resuming (if metadata provided) ──
    nfo_generated = False
    if preview_data:
        try:
            if is_movie:
                # ── Movie mode: extract metadata + generate movie.nfo ──
                movie_entry = next(
                    v for v in search_results.values()
                    if isinstance(v, dict) and v.get("media_type") == "movie"
                )
                tmdb_info = movie_entry.get("tmdb", {})
                tmdb_id = tmdb_info.get("id", 0)
                from ..services.nfo.generator import sanitize_path_name

                tmdb_name = sanitize_path_name(tmdb_info.get("name", "Unknown"))
                bangumi_ids = movie_entry.get("bangumi_ids", [])
                bangumi_id = bangumi_ids[0] if bangumi_ids else 0

                # Movie output path: {MOVIE_HARDLINK_PATH}/{tmdb_name}/
                movie_output_dir = Path(config.MOVIE_HARDLINK_PATH) / tmdb_name
                movie_output_dir.mkdir(parents=True, exist_ok=True)

                from ..services.nfo.nfo_xml import generate_movie_nfo
                nfo_path = generate_movie_nfo(
                    tmdb_id=tmdb_id,
                    bangumi_id=bangumi_id,
                    output_dir=str(movie_output_dir),
                )
                nfo_generated = True
                movie_meta = {
                    "tmdb_id": tmdb_id,
                    "tmdb_name": tmdb_name,
                    "bangumi_id": bangumi_id,
                }
                logger.info(
                    "预生成电影 NFO [%s]: %s (tmdb=%d, bangumi=%d)",
                    torrent_name, nfo_path, tmdb_id, bangumi_id,
                )
            else:
                # Build episode list for batch_nfo_generator
                nfo_episodes: list[dict] = []
                for f in files:
                    if f.get("is_subtitle"):
                        continue
                    # Resolve bangumi_subject_id from the file's bangumi_id
                    # (the search_result's bangumi.id for this show_name)
                    bgm_id = f.get("bangumi_id", 0)
                    nfo_episodes.append({
                        "bangumi_subject_id": bgm_id,
                        "bangumi_episode_sort": f.get("bangumi_sort", 0),
                        "tvdb_id": f.get("tvdb_season") and f.get("tvdb_episode") and _find_tvdb_id(preview_data, bgm_id) or 0,
                        "tvdb_season": f.get("tvdb_season"),     # None if not provided — 0 is valid (Specials)
                        "tvdb_episode": f.get("tvdb_episode"),   # None if not provided
                        "tmdb_id": _find_tmdb_id(preview_data, f.get("tmdb_show_name", "")),
                        "tmdb_season": f.get("tmdb_season", 0),
                        "tmdb_episode": f.get("tmdb_episode", 0),
                    })

                if nfo_episodes:
                    from ..services.nfo.generator import batch_nfo_generator
                    summary = await batch_nfo_generator(hardlink_root, nfo_episodes, series_name=series_name)
                    nfo_generated = True
                    logger.info(
                        "预生成元数据完成 [%s]: NFO=%d, images=%d",
                        torrent_name,
                        summary.get("nfoGenerated", 0),
                        summary.get("imagesDownloaded", 0),
                    )
        except Exception as e:
            logger.warning("预生成元数据失败 [%s]: %s — 将在下载完成后重试", torrent_name, e)

    # ── Resume download ──
    try:
        await resume_torrent(client, info_hash)
        logger.info("下载已恢复 [%s]", torrent_name)
    except Exception as e:
        raise HTTPException(500, f"恢复下载失败: {e}")

    # ── Start background monitor ──
    task = asyncio.create_task(
        _monitor_download(
            info_hash=info_hash,
            torrent_name=torrent_name,
            files=files,
            uploaded_subtitles=uploaded_subtitles,
            hardlink_root=config.MOVIE_HARDLINK_PATH if is_movie else hardlink_root,
            series_name=series_name,
            skip_nfo=nfo_generated,
            movie_meta=movie_meta,
        )
    )
    state._download_tasks[info_hash] = task

    # Clean up the temp torrent file (already added to qBittorrent)
    Path(torrent_path).unlink(missing_ok=True)

    return {
        "ok": True,
        "info_hash": info_hash,
        "message": f"种子已添加，选择性下载 {len(download_indices)}/{len(qb_files)} 个文件。下载完成后自动创建硬链接。",
    }


# ── /api/rss/bangumi/{id} ──

@app.get("/api/rss/search")
async def search_bangumi(q: str):
    """Search bangumi_mikan_map by name. Returns up to 20 matches."""
    return data.search_by_name(q)


@app.get("/api/rss/mikan-search")
async def search_mikan(q: str = ""):
    """Search Mikan by name and return matching anime entries.

    Proxies to Mikan's own search page and parses the HTML.
    Returns a list of {mikan_id, title, url} dicts.
    """
    if not q.strip():
        return []
    try:
        results = await mikan_client.search_mikan(q.strip())
        return [MikanSearchResult(**r) for r in results]
    except Exception as e:
        raise HTTPException(502, f"Mikan 搜索失败: {e}")


@app.get("/api/rss/bangumi/{bangumi_id}/meta")
async def get_bangumi_meta(bangumi_id: int):
    """Fetch Bangumi subject metadata (air_date, eps, rating, series_name).
    Independent from the main RSS lookup — called in parallel by the frontend.
    """
    try:
        subject = await bgm_client.get_subject(bangumi_id)
        series_name = subject.get("name_cn") or subject.get("name", "")
        images = subject.get("images") or {}
        poster_url = (images.get("small") or images.get("grid") or images.get("medium") or "")
        return {
            "air_date": subject.get("date", "") or "",
            "eps": subject.get("eps") or subject.get("total_episodes") or 0,
            "rating": (subject.get("rating") or {}).get("score", 0) or 0,
            "rating_total": (subject.get("rating") or {}).get("total", 0) or 0,
            "series_name": series_name,
            "poster_url": poster_url,
        }
    except Exception as e:
        raise HTTPException(502, f"Bangumi API 失败: {e}")


@app.get("/api/rss/bangumi/{bangumi_id}", response_model=BangumiRssResponse)
async def get_bangumi_rss(bangumi_id: int):
    """Look up Mikan subtitle groups and their RSS URLs for a Bangumi subject ID.

    Maps Bangumi subject ID → Mikan ID via bangumi-data, then scrapes the
    Mikan page to extract all subtitle groups and their RSS feed URLs.
    """
    result = await rss_service.lookup_bangumi_rss(bangumi_id)
    if result is None:
        raise HTTPException(404, f"未找到 Bangumi ID {bangumi_id} 对应的 Mikan 条目")
    return BangumiRssResponse(**result)


@app.post("/api/rss/bangumi/{bangumi_id}/assign-mikan", response_model=BangumiRssResponse)
async def assign_mikan_id(bangumi_id: int, body: AssignMikanRequest):
    """Assign a Mikan ID to a Bangumi entry and return subtitle groups.

    Saves the mapping to bangumi_mikan_map.json so future lookups work.
    Then fetches subtitle groups from Mikan for the given mikan_id.
    """
    name = data.get_bangumi_name(bangumi_id)
    if not name:
        raise HTTPException(404, f"Bangumi ID {bangumi_id} 不存在于映射表中")

    if not data.set_mikan_id(bangumi_id, body.mikan_id):
        raise HTTPException(404, f"Bangumi ID {bangumi_id} 不存在于映射表中")

    result = await rss_service.lookup_mikan_rss(body.mikan_id, bangumi_id, name)
    if not result["groups"]:
        raise HTTPException(404, "该 Mikan ID 对应的条目为空")
    return BangumiRssResponse(**result)


@app.get("/api/rss/data-status")
async def rss_data_status():
    """Check whether the bangumi-data mapping file exists."""
    from ..data import _MAP_FILE
    exists = _MAP_FILE.exists()
    count = 0
    if exists:
        import json
        try:
            raw = json.loads(_MAP_FILE.read_text(encoding="utf-8"))
            count = len(raw)
        except Exception:
            pass
    return {"exists": exists, "count": count}


@app.post("/api/rss/download-data")
async def rss_download_data():
    """Download the latest bangumi-data and rebuild the Mikan mapping."""
    script = Path(__file__).parent.parent / "scripts" / "download_bangumi_data.py"
    if not script.exists():
        raise HTTPException(500, f"下载脚本不存在: {script}")

    try:
        proc = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(500, "下载超时，请重试")

    if proc.returncode != 0:
        raise HTTPException(500, f"下载失败:\n{proc.stderr or proc.stdout}")

    # Clear the in-memory cache so it reloads
    from .. import data as data_module
    data_module._bangumi_mikan_map = None

    return {"ok": True, "output": proc.stdout}


# ── /api/rss/subscriptions ──

@app.get("/api/rss/subscriptions", response_model=list[SubscriptionOut])
async def list_subscriptions():
    """List all saved RSS subscriptions (with downloaded episode counts)."""
    subs = data.list_subscriptions()
    for s in subs:
        eps = data.get_all_episodes(s["bangumi_id"])
        s["downloaded_count"] = len(eps)
    return subs


@app.post("/api/rss/subscriptions", response_model=SubscriptionOut, status_code=201)
async def create_subscription(body: SubscriptionIn):
    """Add or update a subscription.  The body is the complete desired state."""
    sub = data.add_subscription(
        name=body.name,
        rss_url=body.rss_url,
        bangumi_id=body.bangumi_id,
        subgroup_id=body.subgroup_id,
        subgroup_name=body.subgroup_name,
        filter_tags=body.filter_tags,
        backup_rss_url=body.backup_rss_url,
        backup_subgroup_id=body.backup_subgroup_id,
        backup_subgroup_name=body.backup_subgroup_name,
        backup_filter_tags=body.backup_filter_tags,
        download_path=body.download_path,
        exclude_patterns=body.exclude_patterns,
        backup_exclude_patterns=body.backup_exclude_patterns,
    )
    # Enrichment is done asynchronously via the enrich-stream endpoint.
    # The subscription is returned immediately without enrichment data.
    # If a sibling subscription already has cached bgm_season, copy it.
    all_subs = data.list_subscriptions()
    for s in all_subs:
        if s["bangumi_id"] == body.bangumi_id and "bgm" in s:
            cached = {g: s[g] for g in ENRICH_GROUPS if g in s}
            data.update_subscription(body.bangumi_id, cached)
            sub.update(cached)
            break

    # Fetch Bangumi poster CDN URL (non-fatal: falls back to gradient placeholder)
    try:
        poster_url = await image_service.get_subscription_poster_url(body.bangumi_id)
        if poster_url:
            data.update_subscription(body.bangumi_id, {"poster_url": poster_url})
            sub["poster_url"] = poster_url
    except Exception:
        pass  # Non-fatal: frontend falls back to gradient placeholder

    return sub


@app.post("/api/rss/manual-subscribe", response_model=SubscriptionOut, status_code=201)
async def manual_subscribe(body: ManualSubscribeIn):
    """Create a subscription with manually provided RSS URLs.

    Used when Mikan search returns no results and the user enters RSS
    URLs directly.  No subtitle group is associated (subgroup_id = 0).
    """
    sub = data.add_subscription(
        name=body.name,
        rss_url=body.rss_url,
        bangumi_id=body.bangumi_id,
        subgroup_id=0,
        subgroup_name="手动",
        backup_rss_url=body.backup_rss_url or "",
    )
    eps = data.get_all_episodes(sub["bangumi_id"])
    sub["downloaded_count"] = len(eps)
    return SubscriptionOut(**sub)


ENRICH_GROUPS = ("bgm", "tvdb", "tmdb", "series_name")


def _get_cached_enrichment(bangumi_id: int) -> dict | None:
    """Return cached enrichment fields if this bangumi_id already has them.

    When a subscription already has enrichment data (e.g. from a sibling
    primary/backup subscription), we can skip the full Bangumi API chain.

    Only returns cached data that looks valid — a failed enrichment
    (sortrange [0,0] with no IDs) is treated as no cache so it can be
    retried on the next attempt.
    """
    subs = data.list_subscriptions()
    for s in subs:
        if s.get("bangumi_id") != bangumi_id:
            continue
        bgm = s.get("bgm")
        if not bgm:
            continue
        tvdb = s.get("tvdb", {})
        tmdb = s.get("tmdb", {})
        # A valid enrichment has at least one of: episode range, TVDB ID, or TMDB ID
        has_eps = (bgm.get("sortrange") or [0, 0])[1] > 0
        has_tvdb = (tvdb.get("id") or 0) > 0
        has_tmdb = (tmdb.get("id") or 0) > 0
        if has_eps or has_tvdb or has_tmdb:
            return {g: s[g] for g in ENRICH_GROUPS if g in s}
        # Stale/failed enrichment — ignore and re-run
        return None
    return None


@app.post("/api/rss/subscriptions/{bangumi_id}/enrich-stream")
async def enrich_subscription_stream(bangumi_id: int):
    """Stream enrichment progress as NDJSON (one JSON object per line).

    The client reads the response body line by line.  Each line is a
    JSON object with ``type``:

    - ``{"type": "step", "message": "✅ bgm_season=2"}`` — progress update
    - ``{"type": "done", "result": {...}}`` — enrichment succeeded
    - ``{"type": "error", "message": "..."}`` — enrichment failed
    """

    async def generate():
        # Check for cached enrichment — if this bangumi_id already has
        # enrichment data from a sibling subscription, skip the expensive
        # Bangumi API chain.  We still compute RSS offsets because they
        # depend on the RSS feed contents, not on Bangumi metadata, and may
        # have been wiped (e.g. when adding a second feed via add_subscription).
        cached = _get_cached_enrichment(bangumi_id)
        if cached:
            yield (_json.dumps({"type": "step", "message": "Using cached enrichment"}, ensure_ascii=False) + "\n").encode("utf-8")

            # Re-compute RSS offsets from cached bgm data + current RSS URLs
            subs = data.list_subscriptions()
            sub = next((s for s in subs if s["bangumi_id"] == bangumi_id), None)
            primary_rss = sub.get("primary", {}).get("rss_url", "") if sub else ""
            backup_rss = sub.get("backup", {}).get("rss_url", "") if sub else ""

            primary_offset: int | None = None
            backup_offset: int | None = None
            bgm_sortrange = cached.get("bgm", {}).get("sortrange")
            air_date = cached.get("bgm", {}).get("air_date", "")
            if bgm_sortrange and bgm_sortrange[0] > 0 and air_date:
                first_sort = bgm_sortrange[0]
                if primary_rss:
                    smallest = await _compute_rss_offset(primary_rss, air_date)
                    if smallest is not None:
                        primary_offset = first_sort - smallest
                if backup_rss:
                    smallest = await _compute_rss_offset(backup_rss, air_date)
                    if smallest is not None:
                        backup_offset = first_sort - smallest

            if primary_offset is not None:
                data.set_subscription_rss_offset(bangumi_id, "primary", primary_offset)
            if backup_offset is not None:
                data.set_subscription_rss_offset(bangumi_id, "backup", backup_offset)

            cached["primary_offset"] = primary_offset
            cached["backup_offset"] = backup_offset
            yield (_json.dumps({"type": "done", "result": cached}, ensure_ascii=False) + "\n").encode("utf-8")
            return

        queue: asyncio.Queue = asyncio.Queue()

        def on_progress(msg: str):
            queue.put_nowait({"type": "step", "message": msg})

        async def run():
            try:
                # Look up subscription to get RSS URLs for offset computation
                subs = data.list_subscriptions()
                sub = next((s for s in subs if s["bangumi_id"] == bangumi_id), None)
                primary_rss = sub.get("primary", {}).get("rss_url", "") if sub else ""
                backup_rss = sub.get("backup", {}).get("rss_url", "") if sub else ""

                result = await downloader.enrich_subscription(
                    bangumi_id, on_progress=on_progress,
                    primary_rss_url=primary_rss,
                    backup_rss_url=backup_rss,
                )
                if result:
                    # Pop offsets before top-level update_subscription
                    primary_offset = result.pop("primary_offset", None)
                    backup_offset = result.pop("backup_offset", None)
                    data.update_subscription(bangumi_id, result)
                    # Write offsets into nested primary/backup keys
                    if primary_offset is not None:
                        data.set_subscription_rss_offset(bangumi_id, "primary", primary_offset)
                    if backup_offset is not None:
                        data.set_subscription_rss_offset(bangumi_id, "backup", backup_offset)
                    # Restore for the stream response
                    result["primary_offset"] = primary_offset
                    result["backup_offset"] = backup_offset
                queue.put_nowait({"type": "done", "result": result})
            except Exception as exc:
                queue.put_nowait({"type": "error", "message": str(exc)})

        asyncio.create_task(run())

        while True:
            evt = await queue.get()
            line = _json.dumps(evt, ensure_ascii=False) + "\n"
            yield line.encode("utf-8")
            if evt["type"] in ("done", "error"):
                break

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/rss/tmdb-search")
async def search_tmdb_shows(q: str) -> list[TmdbSearchResult]:
    """Search TMDB for TV shows (for manual TMDB ID assignment).

    Used by the frontend Tier-2 manual fallback when a subscription's
    TMDB ID could not be auto-inferred during enrichment.
    """
    from .clients import tmdb as tmdb_client
    try:
        res = await tmdb_client.search_tv(q, language="zh-CN")
        data_json = res.json()
    except Exception as e:
        raise HTTPException(502, f"TMDB search failed: {e}")

    results: list[TmdbSearchResult] = []
    for r in data_json.get("results", [])[:10]:
        results.append(TmdbSearchResult(
            id=r["id"],
            name=r.get("name", ""),
            original_name=r.get("original_name", ""),
            first_air_date=r.get("first_air_date", ""),
            poster_path=r.get("poster_path", ""),
        ))
    return results


@app.patch("/api/rss/subscriptions/{bangumi_id}/tmdb")
async def set_subscription_tmdb(bangumi_id: int, body: SetTmdbRequest):
    """Manually set the TMDB ID (and optional season) for a subscription.

    Persists to both subscriptions.json and bangumi_mikan_map.json.
    Used by the Tier-2 manual override in the frontend.
    """
    # Update the subscription record
    fields: dict = {"tmdb": {"id": body.tmdb_id}}
    if body.tmdb_season is not None:
        fields["tmdb"]["season"] = body.tmdb_season
    ok = data.update_subscription(bangumi_id, fields)
    if not ok:
        raise HTTPException(404, f"Subscription not found: {bangumi_id}")

    # Also persist to bangumi_mikan_map so future auto-lookups work
    data.set_tmdb_id(bangumi_id, body.tmdb_id, body.tmdb_season)

    logger.info(
        "manual tmdb override: bangumi=%d → tmdb_id=%d season=%s",
        bangumi_id, body.tmdb_id, body.tmdb_season,
    )
    return {"ok": True}


@app.delete("/api/rss/subscriptions/{bangumi_id}")
async def delete_subscription(bangumi_id: int, delete_files: bool = False):
    """Remove an RSS subscription by Bangumi ID.

    If *delete_files* is True, also:
    - Delete all related torrents from qBittorrent (with files)
    - Clear download history for this bangumi_id
    """
    if delete_files:
        eps = data.get_all_episodes(bangumi_id)
        hashes = [e["info_hash"] for e in eps.values() if e.get("info_hash")]
        if hashes:
            try:
                qb = await qb_login(config.QBITTORRENT_URL, config.QBITTORRENT_USERNAME, config.QBITTORRENT_PASSWORD)
                for h in hashes:
                    try:
                        await delete_torrent(qb, str(h), delete_files=True)
                    except Exception:
                        pass  # best-effort per torrent
            except Exception as e:
                print(f"⚠️ qBittorrent 连接失败，跳过种子删除: {e}")
        data.clear_download_history(bangumi_id)

    if data.remove_subscription(bangumi_id):
        return {"ok": True}
    raise HTTPException(404, "订阅不存在")


@app.get("/api/rss/feed", response_model=RssFeedResponse)
async def get_rss_feed(
    url: str,
    subscription_id: str | None = None,
    tags: str | None = None,
    exclude_patterns: str = "",
):
    """Fetch and parse a Mikan RSS feed.

    If *subscription_id* is provided, uses that sub's filter tags.
    Otherwise *tags* can be passed directly (comma-separated) for preview.
    *exclude_patterns* is comma-separated and merged with global settings.
    """
    filter_tags: list[str] | None = None
    extra_exclude: list[str] | None = None
    if subscription_id:
        subs = data.list_subscriptions()
        for s in subs:
            if s["bangumi_id"] == int(subscription_id):
                filter_tags = s.get("primary", {}).get("filter_tags", [])
                break
    elif tags:
        filter_tags = [t.strip() for t in tags.split(",") if t.strip()]
    if not filter_tags:
        filter_tags = None
    if exclude_patterns:
        extra_exclude = [p.strip() for p in exclude_patterns.split(",") if p.strip()]
    try:
        return await rss_service.fetch_and_parse_rss(
            url, filter_tags, extra_exclude_patterns=extra_exclude,
        )
    except Exception as e:
        raise HTTPException(502, f"RSS 获取失败: {e}")




# ── /api/rss/subscriptions/{bangumi_id}/history ──

@app.get("/api/rss/subscriptions/{bangumi_id}/history")
async def subscription_history(bangumi_id: int):
    """Return download history for a subscription, enriched with qBittorrent status."""

    # 1. Subscription info
    subs = data.list_subscriptions()
    sub = next((s for s in subs if s["bangumi_id"] == bangumi_id), None)
    name = sub["name"] if sub else str(bangumi_id)
    bgm_season = sub.get("bgm", {}).get("season", 1) if sub else 1
    bgm_sortrange = sub.get("bgm", {}).get("sortrange", [0, 0]) if sub else [0, 0]

    # 2. Download history
    episodes_raw = data.get_all_episodes(bangumi_id)
    hashes = []
    entries = []
    for sort_str, ep in episodes_raw.items():
        h = ep.get("info_hash", "")
        entries.append({
            "sort": int(sort_str),
            "source": ep.get("source", ""),
            "guid": ep.get("guid", ""),
            "at": ep.get("at", ""),
            "info_hash": h,
        })
        if h:
            hashes.append(h)

    # 3. Query qBittorrent
    qbit_info = {}
    if hashes:
        try:
            qb = await qb_login(config.QBITTORRENT_URL, config.QBITTORRENT_USERNAME, config.QBITTORRENT_PASSWORD)
            qbit_info = await get_torrents_by_hashes(qb, hashes)
        except Exception:
            pass

    # 4. Merge
    for e in entries:
        h = e["info_hash"]
        e["qbit"] = qbit_info.get(h) if h else None

    # 5. Missing sorts in range
    downloaded_sorts = {e["sort"] for e in entries}
    missing = []
    if bgm_sortrange[0] > 0:
        for s in range(bgm_sortrange[0], bgm_sortrange[1] + 1):
            if s not in downloaded_sorts:
                missing.append(s)

    return {
        "bangumi_id": bangumi_id,
        "name": name,
        "bgm_season": bgm_season,
        "bgm_sortrange": bgm_sortrange,
        "episodes": entries,
        "missing_sorts": missing,
    }


@app.get("/api/rss/tmdb/{tmdb_id}/seasons")
async def get_tmdb_seasons(tmdb_id: int) -> dict:
    """Fetch all TMDB seasons and episodes for a TV show.

    Calls build_season_episode_map to get every season's episode list,
    then converts to SeasonInfo / TmdbEpisodeInfo Pydantic models.
    Includes a ``_show_name`` sentinel key so the frontend can display
    the show title (e.g. "xxx (83121)") without a second round-trip.
    """
    season_map = await tmdb_service.build_season_episode_map(tmdb_id)
    result: dict = {}
    for sk, sv in season_map.items():
        episodes = [
            TmdbEpisodeInfo(
                epNum=e["epNum"],
                name=e["name"],
                tmdbId=e["tmdbId"],
                overview=e.get("overview") or "",
                airDate=e.get("airDate") or "",
                runtime=e.get("runtime") or 0,
                stillPath=e.get("stillPath") or "",
            )
            for e in sv.get("episodes", [])
        ]
        result[str(sk)] = SeasonInfo(
            name=sv.get("name", f"Season {sk}"), episodes=episodes,
        )

    # Attach show name so the frontend can display "中文名 (ID)"
    try:
        from .clients import tmdb as _tmdb
        _detail_res = await _tmdb.get_tv_detail(tmdb_id)
        _detail = _detail_res.json()
        result["_show_name"] = _detail.get("name", str(tmdb_id))
    except Exception:
        result["_show_name"] = str(tmdb_id)

    return result


@app.get("/api/rss/subscriptions/{bangumi_id}/history-stream")
async def subscription_history_stream(bangumi_id: int):
    """Stream download history + live qBittorrent updates as NDJSON.

    Events:
    - ``{"type": "data", ...}`` — full initial payload (subscription info,
      download history with qBittorrent status)
    - ``{"type": "update", "episodes": [...]}`` — periodic torrent status
      updates (only ``sort`` and ``qbit`` fields per episode)
    """

    async def generate():
        # ── Build initial data (same logic as /history) ──
        subs = data.list_subscriptions()
        sub = next((s for s in subs if s["bangumi_id"] == bangumi_id), None)
        name = sub["name"] if sub else str(bangumi_id)
        bgm_season = sub.get("bgm", {}).get("season", 1) if sub else 1
        bgm_sortrange = sub.get("bgm", {}).get("sortrange", [0, 0]) if sub else [0, 0]

        episodes_raw = data.get_all_episodes(bangumi_id)
        hashes = []
        entries = []
        for sort_str, ep in episodes_raw.items():
            h = ep.get("info_hash", "")
            entries.append({
                "sort": int(sort_str),
                "source": ep.get("source", ""),
                "guid": ep.get("guid", ""),
                "at": ep.get("at", ""),
                "info_hash": h,
                "tmdb_ep": ep.get("tmdb_ep"),
                "tmdb_season": ep.get("tmdb_season"),
            })
            if h:
                hashes.append(h)

        async def _fetch_qbit() -> dict[str, dict]:
            if not hashes:
                return {}
            try:
                qb = await qb_login(
                    config.QBITTORRENT_URL,
                    config.QBITTORRENT_USERNAME,
                    config.QBITTORRENT_PASSWORD,
                )
                return await get_torrents_by_hashes(qb, hashes)
            except Exception:
                return {}

        # Merge qBittorrent into entries
        def _merge_qbit(eps: list[dict], qbit: dict[str, dict]) -> None:
            for e in eps:
                h = e["info_hash"]
                e["qbit"] = qbit.get(h) if h else None

        qbit_info = await _fetch_qbit()
        _merge_qbit(entries, qbit_info)

        # Missing sorts
        downloaded_sorts = {e["sort"] for e in entries}
        missing = []
        if bgm_sortrange[0] > 0:
            for s in range(bgm_sortrange[0], bgm_sortrange[1] + 1):
                if s not in downloaded_sorts:
                    missing.append(s)

        # Send initial data frame
        line = _json.dumps({
            "type": "data",
            "bangumi_id": bangumi_id,
            "name": name,
            "bgm_season": bgm_season,
            "bgm_sortrange": bgm_sortrange,
            "episodes": entries,
            "missing_sorts": missing,
        }, ensure_ascii=False) + "\n"
        yield line.encode("utf-8")

        # ── Periodic qBittorrent updates ──
        try:
            while True:
                await asyncio.sleep(5)

                qbit_info = await _fetch_qbit()
                # Build slim update: only sort + qbit per episode
                updates = []
                for e in entries:
                    h = e["info_hash"]
                    new_qbit = qbit_info.get(h) if h else None
                    if new_qbit != e.get("qbit"):
                        e["qbit"] = new_qbit
                        updates.append({"sort": e["sort"], "qbit": new_qbit})

                if updates:
                    line = _json.dumps({
                        "type": "update",
                        "episodes": updates,
                    }, ensure_ascii=False) + "\n"
                    yield line.encode("utf-8")

        except asyncio.CancelledError:
            # Client disconnected — clean exit
            pass

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.patch("/api/rss/subscriptions/{bangumi_id}/activate")
async def activate_subscription(bangumi_id: int):
    """Re-activate a completed subscription (set active=1)."""
    ok = data.update_subscription(bangumi_id, {"active": 1})
    if not ok:
        raise HTTPException(404, "订阅不存在")
    return {"ok": True}


@app.patch("/api/rss/subscriptions/{bangumi_id}")
async def update_subscription_fields(bangumi_id: int, fields: dict[str, object]):
    """Update specific fields of a subscription (e.g. exclude_patterns)."""
    ok = data.update_subscription(bangumi_id, fields)
    if not ok:
        raise HTTPException(404, "订阅不存在")
    return {"ok": True}


@app.delete("/api/rss/subscriptions/{bangumi_id}/rss")
async def delete_subscription_rss(bangumi_id: int, type: str = "primary"):
    """Clear primary or backup RSS from a subscription.

    If no RSS remains after clearing, the entire subscription is deleted.
    """
    subs = data.list_subscriptions()
    sub = next((s for s in subs if s["bangumi_id"] == bangumi_id), None)
    if not sub:
        raise HTTPException(404, "订阅不存在")

    if type == "primary":
        fields = {"primary": {"rss_url": "", "subgroup_id": 0, "subgroup_name": "",
                              "filter_tags": [], "exclude_patterns": []}}
    else:
        fields = {"backup": {"rss_url": "", "subgroup_id": 0,
                             "subgroup_name": "", "filter_tags": [],
                             "exclude_patterns": []}}

    data.update_subscription(bangumi_id, fields)

    # Reload and check if any RSS remains
    subs = data.list_subscriptions()
    sub = next((s for s in subs if s["bangumi_id"] == bangumi_id), None)
    if sub and not sub.get("primary", {}).get("rss_url") and not sub.get("backup", {}).get("rss_url"):
        data.remove_subscription(bangumi_id)
        return {"ok": True, "deleted": True}

    return {"ok": True, "deleted": False}


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

    # ── Optional NFO regeneration ──
    if regen_nfo:
        subs = data.list_subscriptions()
        sub = next((s for s in subs if s["bangumi_id"] == bangumi_id), None)
        if sub:
            ep = data.get_all_episodes(bangumi_id).get(str(sort), {})
            info_hash = ep.get("info_hash", "")
            if info_hash and sub.get("tmdb", {}).get("id"):
                try:
                    show_name = sub.get("name", str(bangumi_id))
                    series_name = sub.get("series_name") or show_name
                    bgm_season = sub.get("bgm", {}).get("season", 1)
                    rss_base = config.RSS_DOWNLOAD_PATH or config.QBITTORRENT_SAVE_PATH
                    sub_path = f"{series_name}/Season {bgm_season}"
                    qb = await qb_login(
                        config.QBITTORRENT_URL,
                        config.QBITTORRENT_USERNAME,
                        config.QBITTORRENT_PASSWORD,
                    )
                    files = await get_torrent_files(qb, info_hash)
                    old_path = files[0]["name"] if files else ep.get("guid", "")
                    await downloader.generate_metadata(
                        qb, info_hash, bangumi_id, sort,
                        bangumi_id, sub["tmdb"]["id"], show_name,
                        old_path, ep.get("guid", ""),
                        bgm_season=bgm_season,
                        tmdb_season=sub.get("tmdb", {}).get("season"),
                        season_dir=_season_dir, show_dir=_show_dir,
                        series_name=series_name,
                    )
                    logger.info("overrides+PATCH: NFO regenerated for bangumi=%d sort=%d", bangumi_id, sort)
                except Exception:
                    logger.exception("overrides+PATCH: NFO regeneration failed")


# System routes (scan, watch, update, SPA) — MUST be last
app.include_router(system_router)
