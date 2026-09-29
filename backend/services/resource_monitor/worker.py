"""Periodic resource collection, separate from the RSS download worker."""

import asyncio
import logging

from ... import config
from ...db import resource_recognitions
from .collector import collect_source
from .recognize import recognize_resource
from ...db.resource_sources import list_sources

logger = logging.getLogger(__name__)
DEFAULT_POLL_INTERVAL_MIN = 1440
_task: asyncio.Task | None = None
_manual_task: asyncio.Task | None = None
_lock = asyncio.Lock()
_schedule_changed = asyncio.Event()
_status = {"running": False, "polling": False, "last_result": [], "errors": []}


def status() -> dict:
    return {**_status, "poll_interval_min": interval_minutes()}


def interval_minutes() -> int:
    return max(1, min(int(config.RESOURCE_POLL_INTERVAL_MIN), 1440))


def set_interval(minutes: int) -> dict:
    if not 1 <= minutes <= 1440:
        raise ValueError("轮询间隔必须在 1 到 1440 分钟之间")
    config.update({"RESOURCE_POLL_INTERVAL_MIN": minutes})
    notify_interval_changed()
    return status()


def notify_interval_changed() -> None:
    _schedule_changed.set()


async def run_once() -> dict:
    if _lock.locked():
        return status()
    async with _lock:
        _status["polling"] = True
        _status["errors"] = []
        results = []
        try:
            for source in list_sources():
                try:
                    results.append(await collect_source(source))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.exception("Resource feed failed: %s", source)
                    _status["errors"].append(f"{source}: {exc}")
            recognized = 0
            for record in resource_recognitions.list_unrecognized_resources():
                try:
                    result = await recognize_resource(record)
                    if result["status"] == "complete":
                        recognized += 1
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.exception("Resource recognition failed: id=%s", record["id"])
                    _status["errors"].append(f"资源 {record['id']}: {exc}")
            _status["recognized"] = recognized
            _status["last_result"] = results
            return status()
        finally:
            _status["polling"] = False


async def _loop() -> None:
    while True:
        try:
            await run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Resource poll failed")
        while True:
            _schedule_changed.clear()
            try:
                await asyncio.wait_for(_schedule_changed.wait(), interval_minutes() * 60)
            except asyncio.TimeoutError:
                break


def trigger() -> dict:
    """Start a manual poll in the background so the HTTP request can return promptly."""
    global _manual_task
    if not _lock.locked() and (_manual_task is None or _manual_task.done()):
        _status["polling"] = True
        _manual_task = asyncio.create_task(run_once())
    return status()


def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())
        _status["running"] = True


async def stop() -> None:
    global _task, _manual_task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
    if _manual_task is not None and not _manual_task.done():
        _manual_task.cancel()
        try:
            await _manual_task
        except asyncio.CancelledError:
            pass
    _manual_task = None
    _status["running"] = False
