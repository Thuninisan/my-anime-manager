"""FastAPI server for My Anime Manager.

Endpoints:
    POST /torrent      — upload a .torrent file, process immediately
    POST /scan          — start scanning a directory in the background
    GET  /scan/status   — current scan progress

Usage:
    uvicorn my_anime_manager.api:app --host 0.0.0.0 --port 8000
"""

import asyncio
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import config
from .services.batch_service import process_torrent

app = FastAPI(
    title="My Anime Manager",
    description="TMDB + Bangumi 联动工具，为 Jellyfin 生成 NFO 元数据，支持 qBittorrent",
    version="1.0.0",
)

# ========== background scan state ==========

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


class TorrentResponse(BaseModel):
    ok: bool
    info_hash: str = ""
    show_name: str = ""
    episode_count: int = 0
    output_dir: str = ""
    error: str = ""


class ScanStatus(BaseModel):
    running: bool
    dir: str
    total: int
    processed: int
    deleted: int
    failed: int
    current_file: str
    errors: list[str]


# ========== /torrent ==========

@app.post("/torrent", response_model=TorrentResponse)
async def torrent_upload(file: UploadFile = File(...)):
    """Upload a .torrent file and process it immediately.

    The file is saved to a temp location, processed through the full
    qBittorrent → TMDB → Bangumi pipeline, and the temp file is
    deleted afterwards.
    """
    if not file.filename or not file.filename.endswith(".torrent"):
        raise HTTPException(400, "请上传 .torrent 文件")

    # Save uploaded file to temp location
    with tempfile.NamedTemporaryFile(
        suffix=".torrent", delete=False
    ) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        await process_torrent(tmp_path)
        return TorrentResponse(
            ok=True,
            info_hash="",  # process_torrent doesn't return it currently
            show_name=file.filename,
            output_dir="",
        )
    except Exception as e:
        return TorrentResponse(ok=False, error=str(e))
    finally:
        Path(tmp_path).unlink(missing_ok=True)


# ========== /scan ==========

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


@app.post("/scan")
async def start_scan(dir_path: str = Form(...)):
    """Start scanning a directory for .torrent files in the background.

    Only one scan can run at a time. Returns conflict if a scan is
    already in progress.
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


# ========== /config ==========

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
