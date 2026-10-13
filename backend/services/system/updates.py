"""Cached source update checks and Docker entrypoint restart trigger."""

import asyncio
import logging
import os
import time

from ... import __version__
from .update_check import inspect_remote

logger = logging.getLogger(__name__)
_update_cache: dict = {"checked_at": None, "result": None}


def check_update(force: bool = False):
    """Compare HEAD with the configured remote branch; force bypasses the cache."""
    source_dir = os.environ.get("MAM_SOURCE_DIR", "/app/source")
    branch = os.environ.get("MAM_BRANCH", "main")
    now = time.time()
    cache_key = (source_dir, branch)

    if (not force
            and _update_cache.get("key") == cache_key
            and _update_cache["checked_at"]
            and (now - _update_cache["checked_at"]) < 3600
            and _update_cache["result"]):
        return _update_cache["result"]

    result = {**inspect_remote(source_dir, branch), "current_version": __version__}
    if "error" in result:
        _update_cache.update(checked_at=None, result=None)
    else:
        _update_cache.update(key=cache_key, checked_at=time.time(), result=result)
    return result


def apply_update() -> dict:
    logger.info("Update triggered — shutting down in 1s...")
    asyncio.create_task(_do_restart())
    return {"ok": True, "message": "正在关闭并更新..."}


async def _do_restart():
    await asyncio.sleep(1)
    os._exit(42)
