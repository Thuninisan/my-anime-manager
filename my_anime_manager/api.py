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

from . import config
from .services.batch_service import process_torrent
from .services import rss as rss_service
from .services import downloader
from .services import tmdb as tmdb_service
from .services import image_downloader as image_service
from .clients.qbittorrent import login as qb_login, get_torrents_by_hashes, delete_torrent, add_torrent, resume_torrent, get_torrent_files, set_file_priority
from .utils.torrent_hash import compute_info_hash
from .utils.torrent_file_reader import read_torrent_file_list
import bencodepy
from .clients import bangumi as bgm_client
from . import data

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="My Anime Manager",
    description="TMDB + Bangumi 联动工具，为 Jellyfin 生成 NFO 元数据，支持 qBittorrent",
    version="1.0.0",
)

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

_frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"


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
        global _watch_task
        _watch_task = asyncio.create_task(_watch_worker(watch_dir))


# ═══════════════════════════════════════════════════════════════════════
# Pydantic response models
# ═══════════════════════════════════════════════════════════════════════

class TmdbEpisodeInfo(BaseModel):
    epNum: int
    name: str
    tmdbId: int
    overview: str = ""
    airDate: str = ""
    runtime: int = 0
    stillPath: str = ""


class SeasonInfo(BaseModel):
    name: str
    episodes: list[TmdbEpisodeInfo]


class RssSubtitleGroup(BaseModel):
    name: str
    subgroup_id: int
    rss_url: str


class BangumiRssResponse(BaseModel):
    bangumi_id: int
    name: str
    mikan_id: int
    global_rss: str
    groups: list[RssSubtitleGroup]


class RssFeedItem(BaseModel):
    guid: str
    title: str
    torrent_url: str
    pub_date: str
    size_bytes: int
    downloaded: bool
    tags: list[str]
    passed: bool
    excluded: bool
    episode_number: int = 0


class RssFeedResponse(BaseModel):
    title: str
    items: list[RssFeedItem]


class SubscriptionIn(BaseModel):
    name: str
    rss_url: str
    bangumi_id: int
    subgroup_id: int
    subgroup_name: str
    filter_tags: list[str] = []
    backup_rss_url: str = ""
    backup_subgroup_id: int = 0
    backup_subgroup_name: str = ""
    backup_filter_tags: list[str] = []
    download_path: str = ""
    active: int = 1
    exclude_patterns: list[str] = []
    backup_exclude_patterns: list[str] = []


class SubscriptionOut(BaseModel):
    name: str
    rss_url: str
    bangumi_id: int
    subgroup_id: int
    subgroup_name: str
    filter_tags: list[str] = []
    backup_rss_url: str = ""
    backup_subgroup_id: int = 0
    backup_subgroup_name: str = ""
    backup_filter_tags: list[str] = []
    exclude_patterns: list[str] = []
    backup_exclude_patterns: list[str] = []
    created_at: str = ""
    updated_at: str = ""
    download_path: str = ""
    active: int = 1
    # Pre-computed season metadata (from Bangumi chain)
    bgm_season: int = 1
    bgm_sortrange: list[int] = []
    # Bangumi rating (from subject API)
    bgm_rating: float = 0.0
    bgm_rating_total: int = 0
    tmdb_id: int = 0
    tmdb_season: int | None = None
    # Poster image URL (Bangumi CDN URL, frontend loads directly)
    poster_url: str = ""
    # Downloaded episode count (from download_history.json)
    downloaded_count: int = 0


class ScanStatus(BaseModel):
    running: bool
    dir: str
    total: int
    processed: int
    deleted: int
    failed: int
    current_file: str
    errors: list[str]


# ═══════════════════════════════════════════════════════════════════════
# background scan state
# ═══════════════════════════════════════════════════════════════════════

_scan_task: Optional[asyncio.Task] = None
_scan_status: dict = {
    "running": False,
    "dir": "",
    "total": 0,
    "processed": 0,
    "deleted": 0,
    "failed": 0,
    "current_file": "",
    "errors": [],
}


async def _scan_worker(dir_path: str):
    """Background worker: scan directory and process each .torrent file."""
    global _scan_status
    abs_dir = Path(dir_path).resolve()

    _scan_status = {
        "running": True,
        "dir": str(abs_dir),
        "total": 0,
        "processed": 0,
        "deleted": 0,
        "failed": 0,
        "current_file": "",
        "errors": [],
    }

    if not abs_dir.exists():
        _scan_status["errors"].append(f"目录不存在: {abs_dir}")
        _scan_status["running"] = False
        return

    files = sorted(abs_dir.glob("*.torrent"))
    _scan_status["total"] = len(files)

    if not files:
        _scan_status["running"] = False
        return

    for file in files:
        _scan_status["current_file"] = file.name
        try:
            await process_torrent(str(file))
            file.unlink()
            _scan_status["processed"] += 1
            _scan_status["deleted"] += 1
        except Exception as e:
            _scan_status["failed"] += 1
            _scan_status["errors"].append(f"{file.name}: {e}")

    _scan_status["current_file"] = ""
    _scan_status["running"] = False


# ═══════════════════════════════════════════════════════════════════════
# background watch state
# ═══════════════════════════════════════════════════════════════════════

_watch_task: Optional[asyncio.Task] = None
_watch_status: dict = {
    "running": False,
    "dir": "",
    "processed": 0,
    "deleted": 0,
    "failed": 0,
    "current_file": "",
    "errors": [],
}


async def _watch_worker(dir_path: str):
    """Background worker: continuously watch directory for .torrent files."""
    global _watch_status
    abs_dir = Path(dir_path).resolve()

    if not abs_dir.exists():
        print(f"❌ 监控目录不存在: {abs_dir}")
        _watch_status["running"] = False
        return

    SCAN_INTERVAL = 30
    print(f"👀 开始监控 {abs_dir}，每 {SCAN_INTERVAL}s 扫描一次...")

    _watch_status["running"] = True
    _watch_status["dir"] = str(abs_dir)

    while True:
        files = sorted(abs_dir.glob("*.torrent"))

        if files:
            print(f"📁 扫描到 {len(files)} 个 torrent 文件")
            _watch_status["errors"] = []

            failed_dir = abs_dir / "failed"
            failed_dir.mkdir(exist_ok=True)

            for file in files:
                _watch_status["current_file"] = file.name
                success = False
                try:
                    await process_torrent(str(file))
                    success = True
                except Exception as e:
                    _watch_status["failed"] += 1
                    _watch_status["errors"].append(f"{file.name}: {e!r}")
                    print(f"❌ 处理失败 {file.name}: {e!r}")
                    traceback.print_exc()

                if not file.exists():
                    _watch_status["processed"] += 1
                    _watch_status["deleted"] += 1
                elif success:
                    file.unlink()
                    _watch_status["processed"] += 1
                    _watch_status["deleted"] += 1
                else:
                    dest = failed_dir / file.name
                    if dest.exists():
                        stem = file.stem
                        counter = 1
                        while dest.exists():
                            dest = failed_dir / f"{stem}_{counter}.torrent"
                            counter += 1
                    file.rename(dest)
                    print(f"   ⚠️ 处理失败，已移到 {dest}")

            _watch_status["current_file"] = ""
            print("   继续监控...")

        await asyncio.sleep(SCAN_INTERVAL)


# ═══════════════════════════════════════════════════════════════════════
# Routes
# ═══════════════════════════════════════════════════════════════════════

@app.get("/")
async def root():
    """Health check + status overview, or serve the frontend."""
    # If frontend is built, serve index.html
    index_path = _frontend_dist / "index.html"
    if index_path.exists():
        from fastapi.responses import FileResponse
        return FileResponse(str(index_path))

    return {
        "service": "My Anime Manager",
        "version": "1.0.0",
        "docs": "/docs",
        "watch": {
            "running": _watch_status["running"],
            "dir": _watch_status["dir"],
            "processed": _watch_status["processed"],
            "failed": _watch_status["failed"],
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
        from .services.torrent_preview import parse_and_search
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

# Track active download-monitor tasks so they don't get garbage-collected
_download_tasks: dict[str, asyncio.Task] = {}


def _sanitize_path_component(name: str) -> str:
    """Remove characters that are illegal in directory / file names."""
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip()


async def _monitor_download(
    info_hash: str,
    torrent_name: str,
    files: list[dict],
    uploaded_subtitles: list[dict],
    hardlink_root: str,
):
    """Background task: poll qBittorrent until download completes, then create hardlinks / copy subtitles."""
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
        logger.info("下载进度 [%s]: %.1f%% (%s)", torrent_name, progress * 100, state)

        if progress >= 1.0 or "paused" in state.lower() or "stopped" in state.lower() or "completed" in state.lower():
            if progress < 1.0:
                logger.warning("种子状态异常 (progress=%.2f, state=%s), 仍然尝试创建文件", progress, state)

            save_path = t.get("save_path", hardlink_root)
            logger.info("下载完成 [%s], 开始创建硬链接/复制字幕...", torrent_name)

            created = 0
            for f in files:
                torrent_path = f["torrent_path"]
                is_sub = f.get("is_subtitle", False)
                tmdb_name = _sanitize_path_component(f.get("tmdb_show_name", "Unknown"))
                bgm_name = _sanitize_path_component(f.get("bangumi_show_name", torrent_name))
                bgm_sort = f.get("bangumi_sort", 1)
                src_ext = Path(torrent_path).suffix

                # Destination: {hardlink_root}/{tmdb_name}/{bgm_name}/{bgm_name} {sort:02d}.ext
                dest_dir = Path(hardlink_root) / tmdb_name / bgm_name
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest_filename = f"{bgm_name} {bgm_sort:02d}{src_ext}"
                dest_path = dest_dir / dest_filename

                # Source: qBittorrent save_path / torrent_path
                src_path = Path(save_path) / torrent_path

                try:
                    if src_path.exists():
                        if is_sub:
                            shutil.copy2(src_path, dest_path)
                        else:
                            # Remove existing destination so os.link doesn't fail
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
                tmdb_name = _sanitize_path_component(usub.get("tmdb_show_name", "Unknown"))
                bgm_name = _sanitize_path_component(usub.get("bangumi_show_name", torrent_name))
                bgm_sort = usub.get("bangumi_sort", 1)
                src_sub = subtitle_dir / stored_name

                if not src_sub.exists():
                    logger.warning("   上传的字幕文件不存在: %s", src_sub)
                    continue

                dest_dir = Path(hardlink_root) / tmdb_name / bgm_name
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest_ext = src_sub.suffix
                dest_filename = f"{bgm_name} {bgm_sort:02d}{dest_ext}"
                dest_path = dest_dir / dest_filename

                try:
                    shutil.copy2(src_sub, dest_path)
                    created += 1
                    logger.info("   [uploaded] %s → %s", stored_name, dest_path)
                except OSError as e:
                    logger.error("   复制上传字幕失败: %s → %s — %s", src_sub, dest_path, e)

            logger.info("下载后处理完成 [%s]: 创建了 %d 个文件", torrent_name, created)

            # Clean up the temp torrent file
            # (kept for now; the original torrent_preview used to clean up here)

            # Remove task from tracker
            _download_tasks.pop(info_hash, None)
            return

    logger.warning("下载监控超时 [%s] (24h)", torrent_name)
    _download_tasks.pop(info_hash, None)


@app.post("/api/torrent/download")
async def torrent_download(body: dict):
    """Add a torrent to qBittorrent with selective file download.

    Only the files listed in *files* (and their matching subtitles) are
    downloaded.  After the download completes a background task creates
    hardlinks for video files and copies subtitle files into the configured
    ``TORRENT_HARDLINK_PATH`` directory.
    """
    torrent_path = body.get("torrent_path", "")
    torrent_name = body.get("torrent_name", "")
    files: list[dict] = body.get("files", [])
    uploaded_subtitles: list[dict] = body.get("uploaded_subtitles", [])

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
            hardlink_root=hardlink_root,
        )
    )
    _download_tasks[info_hash] = task

    # Clean up the temp torrent file (already added to qBittorrent)
    Path(torrent_path).unlink(missing_ok=True)

    return {
        "ok": True,
        "info_hash": info_hash,
        "message": f"种子已添加，选择性下载 {len(download_indices)}/{len(qb_files)} 个文件。下载完成后自动创建硬链接。",
    }


# ── /scan ──

@app.post("/scan")
async def start_scan(dir_path: str = Form(...)):
    """Start scanning a directory for .torrent files in the background.

    Only one scan can run at a time.
    """
    global _scan_task
    if _scan_task and not _scan_task.done():
        raise HTTPException(409, "扫描任务已在运行中")

    _scan_task = asyncio.create_task(_scan_worker(dir_path))
    return {"ok": True, "dir": dir_path, "message": "扫描已启动"}


@app.get("/scan/status", response_model=ScanStatus)
async def scan_status():
    """Get the current scan progress."""
    return ScanStatus(**_scan_status)


@app.get("/watch/status")
async def watch_status():
    """Get the current watch loop status (auto-started via WATCH_DIR)."""
    return _watch_status


# ── /config ──

@app.get("/config")
async def get_config():
    """Read all current config values (sensitive fields masked)."""
    return config.get_all()


@app.put("/config")
async def update_config(changes: dict[str, object]):
    """Update config values at runtime.

    Example body:
        {"TMDB_API_KEY": "new-key", "PROXY_PORT": 1080}

    Only known config keys are accepted; unknown keys are silently ignored.
    """
    return config.update(changes)


# ── /api/rss/bangumi/{id} ──

@app.get("/api/rss/search")
async def search_bangumi(q: str):
    """Search bangumi_mikan_map by name. Returns up to 20 matches."""
    return data.search_by_name(q)


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


@app.get("/api/rss/data-status")
async def rss_data_status():
    """Check whether the bangumi-data mapping file exists."""
    from .data import _MAP_FILE
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
    from . import data as data_module
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
        if s["bangumi_id"] == body.bangumi_id and "bgm_season" in s:
            cached = {k: s[k] for k in ("bgm_season", "bgm_sortrange", "tmdb_id", "tmdb_season", "series_name", "bgm_rating", "bgm_rating_total") if k in s}
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


ENRICH_FIELDS = ("bgm_season", "bgm_sortrange", "series_name", "tmdb_id", "tmdb_season", "bgm_rating", "bgm_rating_total")


def _get_cached_enrichment(bangumi_id: int) -> dict | None:
    """Return cached enrichment fields if this bangumi_id already has them.

    When a subscription already has enrichment data (e.g. from a sibling
    primary/backup subscription), we can skip the full Bangumi API chain.
    """
    subs = data.list_subscriptions()
    for s in subs:
        if s["bangumi_id"] == bangumi_id and "bgm_season" in s:
            return {k: s[k] for k in ENRICH_FIELDS if k in s}
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
        # enrichment data from a sibling subscription, return it immediately
        # instead of re-running the full Bangumi API chain.
        cached = _get_cached_enrichment(bangumi_id)
        if cached:
            yield (_json.dumps({"type": "step", "message": "Using cached enrichment"}, ensure_ascii=False) + "\n").encode("utf-8")
            yield (_json.dumps({"type": "done", "result": cached}, ensure_ascii=False) + "\n").encode("utf-8")
            return

        queue: asyncio.Queue = asyncio.Queue()

        def on_progress(msg: str):
            queue.put_nowait({"type": "step", "message": msg})

        async def run():
            try:
                result = await downloader.enrich_subscription(
                    bangumi_id, on_progress=on_progress,
                )
                if result:
                    data.update_subscription(bangumi_id, result)
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
                filter_tags = s.get("filter_tags", [])
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


# ── /api/rss/downloader ──

@app.get("/api/rss/downloader/status")
async def downloader_status():
    return downloader.get_status()


@app.post("/api/rss/downloader/start")
async def downloader_start():
    await downloader.start()
    return {"ok": True}


@app.post("/api/rss/downloader/stop")
async def downloader_stop():
    await downloader.stop()
    return {"ok": True}


@app.post("/api/rss/downloader/run-once")
async def downloader_run_once():
    await downloader.run_once()
    return {"ok": True}


@app.get("/api/rss/downloader/config")
async def downloader_config():
    return downloader.get_config()


class IntervalBody(BaseModel):
    minutes: int

@app.patch("/api/rss/downloader/config")
async def downloader_set_interval(body: IntervalBody):
    return await downloader.set_interval(body.minutes)


@app.get("/api/rss/downloader/qbit-check")
async def downloader_qbit_check():
    return await downloader.check_qbit()


# ── /api/rss/subscriptions/{bangumi_id}/history ──

@app.get("/api/rss/subscriptions/{bangumi_id}/history")
async def subscription_history(bangumi_id: int):
    """Return download history for a subscription, enriched with qBittorrent status."""

    # 1. Subscription info
    subs = data.list_subscriptions()
    sub = next((s for s in subs if s["bangumi_id"] == bangumi_id), None)
    name = sub["name"] if sub else str(bangumi_id)
    bgm_season = sub.get("bgm_season", 1) if sub else 1
    bgm_sortrange = sub.get("bgm_sortrange", [0, 0]) if sub else [0, 0]

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
async def get_tmdb_seasons(tmdb_id: int) -> dict[str, SeasonInfo]:
    """Fetch all TMDB seasons and episodes for a TV show.

    Calls build_season_episode_map to get every season's episode list,
    then converts to SeasonInfo / TmdbEpisodeInfo Pydantic models.
    Returns an empty dict if TMDB has no data for this show.
    """
    season_map = await tmdb_service.build_season_episode_map(tmdb_id)
    result: dict[str, SeasonInfo] = {}
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
        bgm_season = sub.get("bgm_season", 1) if sub else 1
        bgm_sortrange = sub.get("bgm_sortrange", [0, 0]) if sub else [0, 0]

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
        fields = {"rss_url": "", "subgroup_id": 0, "subgroup_name": "",
                   "filter_tags": [], "exclude_patterns": []}
    else:
        fields = {"backup_rss_url": "", "backup_subgroup_id": 0,
                   "backup_subgroup_name": "", "backup_filter_tags": [],
                   "backup_exclude_patterns": []}

    data.update_subscription(bangumi_id, fields)

    # Reload and check if any RSS remains
    subs = data.list_subscriptions()
    sub = next((s for s in subs if s["bangumi_id"] == bangumi_id), None)
    if sub and not sub.get("rss_url") and not sub.get("backup_rss_url"):
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
    bgm_season = sub.get("bgm_season", 1)
    tmdb_id = sub.get("tmdb_id", 0)
    tmdb_season = sub.get("tmdb_season")
    rss_base = config.RSS_DOWNLOAD_PATH or config.QBITTORRENT_SAVE_PATH
    sub_path_template = sub.get("download_path") or "/{series_name}/Season {season}"
    sub_path = sub_path_template.format(
        series_name=series_name, show_name=show_name, season=bgm_season,
    ).strip("/")

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
                    base_path=rss_base,
                    sub_path=sub_path,
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
    bgm_season = sub.get("bgm_season", 1)
    tmdb_id = sub.get("tmdb_id", 0)
    tmdb_season = sub.get("tmdb_season")
    rss_base = config.RSS_DOWNLOAD_PATH or config.QBITTORRENT_SAVE_PATH
    sub_path_template = sub.get("download_path") or "/{series_name}/Season {season}"
    sub_path = sub_path_template.format(
        series_name=series_name, show_name=show_name, season=bgm_season,
    ).strip("/")

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
                    base_path=rss_base, sub_path=sub_path,
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
            if info_hash and sub.get("tmdb_id"):
                try:
                    show_name = sub.get("name", str(bangumi_id))
                    series_name = sub.get("series_name") or show_name
                    bgm_season = sub.get("bgm_season", 1)
                    rss_base = config.RSS_DOWNLOAD_PATH or config.QBITTORRENT_SAVE_PATH
                    sub_path_template = sub.get("download_path") or "/{series_name}/Season {season}"
                    sub_path = sub_path_template.format(
                        series_name=series_name, show_name=show_name, season=bgm_season,
                    ).strip("/")
                    qb = await qb_login(
                        config.QBITTORRENT_URL,
                        config.QBITTORRENT_USERNAME,
                        config.QBITTORRENT_PASSWORD,
                    )
                    files = await get_torrent_files(qb, info_hash)
                    old_path = files[0]["name"] if files else ep.get("guid", "")
                    await downloader.generate_metadata(
                        qb, info_hash, bangumi_id, sort,
                        bangumi_id, sub["tmdb_id"], show_name,
                        old_path, ep.get("guid", ""),
                        bgm_season=bgm_season,
                        tmdb_season=sub.get("tmdb_season"),
                        base_path=rss_base, sub_path=sub_path,
                    )
                    logger.info("overrides+PATCH: NFO regenerated for bangumi=%d sort=%d", bangumi_id, sort)
                except Exception:
                    logger.exception("overrides+PATCH: NFO regeneration failed")

    return {"ok": True}

@app.get("/api/rss/settings")
async def get_rss_settings():
    """Get global RSS settings (exclude patterns, etc.)."""
    return data.get_rss_settings()


@app.put("/api/rss/settings")
async def update_rss_settings(changes: dict[str, object]):
    """Update global RSS settings."""
    return data.update_rss_settings(changes)


# ═══════════════════════════════════════════════════════════════════════
# SPA fallback — MUST be the LAST route registered
# ═══════════════════════════════════════════════════════════════════════

@app.get("/{full_path:path}", include_in_schema=False)
async def serve_frontend(full_path: str):
    """Serve the React SPA for all non-API routes (production mode).

    Registered LAST so that all explicit routes (/config, /api/*, /scan,
    /watch) take priority.  Only fires for frontend navigation
    paths that don't match any other route.
    """
    if not _frontend_dist.exists():
        raise HTTPException(404, "Frontend not built (run: cd frontend && npm run build)")

    # Try to serve the exact file
    file_path = _frontend_dist / full_path
    if file_path.is_file():
        from fastapi.responses import FileResponse
        return FileResponse(str(file_path))

    # SPA fallback: serve index.html for client-side routing
    index_path = _frontend_dist / "index.html"
    if index_path.exists():
        from fastapi.responses import FileResponse
        return FileResponse(str(index_path))

    raise HTTPException(404, "Not found")
