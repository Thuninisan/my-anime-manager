"""Unified application settings API."""

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from .. import config
from ..services.system import settings

router = APIRouter()


@router.get("/api/settings")
async def get_config():
    """Read all current config values (sensitive fields masked)."""
    return config.get_all()


@router.patch("/api/settings")
async def update_config(changes: dict[str, object]):
    try:
        return await settings.update(changes)
    except ValidationError as exc:
        raise HTTPException(422, exc.errors(include_context=False)) from exc
