"""API routes: /config, /api/rss/settings."""

from fastapi import APIRouter

from .. import config
from .. import data

router = APIRouter()


# ── /config ──

@router.get("/config")
async def get_config():
    """Read all current config values (sensitive fields masked)."""
    return config.get_all()


@router.put("/config")
async def update_config(changes: dict[str, object]):
    """Update config values at runtime."""
    return config.update(changes)


# ── /api/rss/settings ──

@router.get("/api/rss/settings")
async def get_rss_settings():
    """Get global RSS settings (exclude patterns etc.)."""
    return data.get_rss_settings()


@router.put("/api/rss/settings")
async def update_rss_settings(changes: dict[str, object]):
    """Update global RSS settings."""
    return data.update_rss_settings(changes)
