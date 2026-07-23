"""API routes: /api/rss/downloader/*."""

from fastapi import APIRouter

from ..services import downloader
from .models import IntervalBody

router = APIRouter()


@router.get("/api/rss/downloader/status")
async def downloader_status():
    """Get RSS downloader running/stopped status."""
    return downloader.get_status()


@router.post("/api/rss/downloader/start")
async def downloader_start():
    """Start periodic RSS download loop."""
    await downloader.start()
    return {"ok": True}


@router.post("/api/rss/downloader/stop")
async def downloader_stop():
    """Stop RSS download loop."""
    await downloader.stop()
    return {"ok": True}


@router.post("/api/rss/downloader/run-once")
async def downloader_run_once():
    """Run a single RSS poll cycle."""
    await downloader.run_once()
    return {"ok": True}


@router.get("/api/rss/downloader/config")
async def downloader_config():
    """Get downloader settings (interval, etc.)."""
    return downloader.get_config()


@router.patch("/api/rss/downloader/config")
async def downloader_set_interval(body: IntervalBody):
    """Set downloader poll interval in minutes."""
    return await downloader.set_interval(body.minutes)


@router.get("/api/rss/downloader/qbit-check")
async def downloader_qbit_check():
    """Test qBittorrent connectivity."""
    return await downloader.check_qbit()
