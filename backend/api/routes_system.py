"""Directory tasks, source updates and SPA fallback endpoints."""

from pathlib import Path

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import FileResponse

from .. import __version__
from ..services.system import directory_tasks, updates
from .models import ScanStatus

router = APIRouter()


@router.post("/scan")
async def start_scan(dir_path: str = Form(...)):
    try:
        directory_tasks.start_scan(dir_path)
    except directory_tasks.ScanInProgress as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True, "dir": dir_path, "message": "扫描已启动"}


@router.get("/scan/status", response_model=ScanStatus)
async def scan_status():
    return ScanStatus(**directory_tasks.scan_status())


@router.get("/watch/status")
async def watch_status():
    return directory_tasks.watch_status()


@router.get("/api/update/check")
def check_update(force: bool = False):
    return updates.check_update(force)


@router.post("/api/update/apply")
async def apply_update():
    return updates.apply_update()


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
