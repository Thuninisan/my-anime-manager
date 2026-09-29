"""Unified application settings API."""

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from .. import config
from ..services import downloader
from ..services.resource_monitor import worker as resource_worker

router = APIRouter()


@router.get("/api/settings")
async def get_config():
    """Read all current config values (sensitive fields masked)."""
    return config.get_all()


@router.patch("/api/settings")
async def update_config(changes: dict[str, object]):
    try:
        result = config.update(changes)
    except ValidationError as exc:
        raise HTTPException(422, exc.errors(include_context=False)) from exc
    if "RSS_POLL_INTERVAL_MIN" in changes:
        await downloader.apply_interval()
    if "RESOURCE_POLL_INTERVAL_MIN" in changes:
        resource_worker.notify_interval_changed()
    return result
