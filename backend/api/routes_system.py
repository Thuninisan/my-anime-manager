"""API routes: /scan, /watch, /api/update, SPA fallback."""

import asyncio
import logging
import os
import time
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import FileResponse

from .. import __version__
from .. import config
from ..services.torrent.batch_service import process_torrent
from ..services.update_check import inspect_remote
from ..logging.logging_config import new_operation_id, operation_context
from .models import ScanStatus
from . import state

router = APIRouter()
logger = logging.getLogger(__name__)


# ── /scan ──

@router.post("/scan")
async def start_scan(dir_path: str = Form(...)):
    """Start scanning a directory for .torrent files in the background."""
    if state._scan_task and not state._scan_task.done():
        raise HTTPException(409, "扫描任务已在运行中")
    state._scan_task = asyncio.create_task(_scan_worker(dir_path))
    return {"ok": True, "dir": dir_path, "message": "扫描已启动"}


@router.get("/scan/status", response_model=ScanStatus)
async def scan_status():
    """Get the current scan progress."""
    return ScanStatus(**state._scan_status)


# ── /watch ──

@router.get("/watch/status")
async def watch_status():
    """Get the current watch loop status."""
    return state._watch_status


# ── /api/update ──

@router.get("/api/update/check")
def check_update(force: bool = False):
    """Compare HEAD with the configured remote branch; force bypasses the cache."""
    source_dir = os.environ.get("MAM_SOURCE_DIR", "/app/source")
    branch = os.environ.get("MAM_BRANCH", "main")
    now = time.time()
    cache_key = (source_dir, branch)

    if (not force
            and state._update_cache.get("key") == cache_key
            and state._update_cache["checked_at"]
            and (now - state._update_cache["checked_at"]) < 3600
            and state._update_cache["result"]):
        return state._update_cache["result"]

    result = {**inspect_remote(source_dir, branch), "current_version": __version__}
    if "error" in result:
        state._update_cache.update(checked_at=None, result=None)
    else:
        state._update_cache.update(key=cache_key, checked_at=time.time(), result=result)
    return result


@router.post("/api/update/apply")
async def apply_update():
    """Exit with code 42 so the Docker entrypoint pulls and restarts."""
    logger.info("Update triggered — shutting down in 1s...")
    asyncio.create_task(_do_restart())
    return {"ok": True, "message": "正在关闭并更新..."}


async def _do_restart():
    await asyncio.sleep(1)
    os._exit(42)


# ── SPA Fallback (must be last route) ──

_frontend_dist = Path(__file__).parent.parent.parent / "frontend" / "dist"


@router.get("/api/version")
async def app_version():
    return {"version": __version__}


@router.get("/{full_path:path}", include_in_schema=False)
async def serve_frontend(full_path: str):
    """Serve React SPA for non-API routes."""
    if not _frontend_dist.exists():
        return {"message": "My Anime Manager API", "version": __version__}
    file_path = _frontend_dist / full_path
    if file_path.exists() and file_path.is_file():
        return FileResponse(str(file_path))
    index = _frontend_dist / "index.html"
    if index.exists():
        return FileResponse(
            str(index),
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )
    return {"message": "My Anime Manager API", "version": __version__}


# ── Scan / Watch workers ──

async def _scan_worker(dir_path: str):
    """Background worker: scan directory and process each .torrent file."""
    with operation_context(new_operation_id("scan")):
        await _scan_worker_with_context(dir_path)


async def _scan_worker_with_context(dir_path: str):
    p = Path(dir_path)
    if not p.exists():
        state._scan_status.update(running=False, errors=["目录不存在: " + dir_path])
        return
    torrents = sorted(p.glob("*.torrent"))
    state._scan_status.update(running=True, dir=dir_path, total=len(torrents),
                               processed=0, deleted=0, failed=0, current_file="", errors=[])
    for tf in torrents:
        state._scan_status["current_file"] = tf.name
        try:
            ok = await process_torrent(str(tf), config.TORRENT_DOWNLOAD_PATH, config.TORRENT_HARDLINK_PATH)
            if ok:
                tf.unlink()
                state._scan_status["deleted"] += 1
            else:
                state._scan_status["failed"] += 1
        except Exception as exc:
            state._scan_status["failed"] += 1
            state._scan_status["errors"].append(f"{tf.name}: {exc}")
            logger.exception("目录扫描处理失败: %s", tf.name)
        state._scan_status["processed"] += 1
    state._scan_status["running"] = False


async def _watch_worker(dir_path: str):
    """Background loop: watch directory for .torrent files every 30s."""
    with operation_context(new_operation_id("watch")):
        await _watch_worker_with_context(dir_path)


async def _watch_worker_with_context(dir_path: str):
    p = Path(dir_path)
    if not p.exists():
        p.mkdir(parents=True, exist_ok=True)
    state._watch_status.update(running=True, dir=dir_path, processed=0, failed=0, errors=[])
    while True:
        try:
            torrents = sorted(p.glob("*.torrent"))
            for tf in torrents:
                state._watch_status["current_file"] = tf.name
                try:
                    ok = await process_torrent(str(tf), config.TORRENT_DOWNLOAD_PATH, config.TORRENT_HARDLINK_PATH)
                    if ok:
                        tf.unlink()
                    else:
                        failed_dir = p / "failed"
                        failed_dir.mkdir(exist_ok=True)
                        tf.rename(failed_dir / tf.name)
                        state._watch_status["failed"] += 1
                except Exception as exc:
                    state._watch_status["failed"] += 1
                    state._watch_status["errors"].append(f"{tf.name}: {exc}")
                    logger.exception("目录监控处理失败: %s", tf.name)
                state._watch_status["processed"] += 1
        except Exception:
            logger.exception("Watch worker error")
        await asyncio.sleep(30)
