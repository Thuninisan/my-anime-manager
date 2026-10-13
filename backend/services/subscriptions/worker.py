"""RSS scheduling, serialization and cumulative worker statistics.

Full polls and first-subscription polls share one lock. Only full polls update
worker statistics; the single-subscription entry returns its own result.
"""

import asyncio
import logging
import time

from ... import config
from ...data import list_subscriptions
from ...logging.logging_config import new_operation_id, operation_context
from . import polling

logger = logging.getLogger(__name__)

_worker_task: asyncio.Task | None = None
_worker_running = False
_worker_status: dict = {
    "running": False,
    "last_run": "",
    "downloaded": 0,
    "errors": [],
    "poll_interval_min": config.RSS_POLL_INTERVAL_MIN,
}
_worker_lock = asyncio.Lock()


def get_status() -> dict:
    return {**_worker_status, "scheduler_running": _worker_running}


def get_config() -> dict:
    _worker_status["poll_interval_min"] = config.RSS_POLL_INTERVAL_MIN
    return {
        "poll_interval_min": _worker_status["poll_interval_min"],
        "running": _worker_running,
    }


async def start(poll_interval_min: int | None = None):
    global _worker_task, _worker_running
    _worker_status["poll_interval_min"] = config.RSS_POLL_INTERVAL_MIN
    if _worker_running:
        return
    _worker_running = True
    interval = _worker_status["poll_interval_min"] * 60
    _worker_task = asyncio.create_task(_run_loop(interval))


async def stop():
    global _worker_task, _worker_running
    _worker_running = False
    if _worker_task:
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
        _worker_task = None


async def set_interval(minutes: int) -> dict:
    """Change polling interval.  Restarts the worker if it's running."""
    config.update({"RSS_POLL_INTERVAL_MIN": minutes})
    await apply_interval()
    return get_config()


async def apply_interval() -> None:
    minutes = config.RSS_POLL_INTERVAL_MIN
    _worker_status["poll_interval_min"] = minutes
    if _worker_running:
        await stop()
        await start(minutes)


async def run_once():
    """Manually trigger one full poll cycle."""
    await _poll_subscriptions()


async def poll_subscription(
    bangumi_id: int, *, primary_feed: dict | None = None,
    backup_feed: dict | None = None,
) -> dict:
    """Poll one saved subscription without changing the full worker status."""
    async with _worker_lock:
        with operation_context(new_operation_id("rss")):
            sub = next((item for item in list_subscriptions()
                        if item["bangumi_id"] == bangumi_id), None)
            if sub is None:
                raise ValueError(f"订阅不存在: {bangumi_id}")
            if sub.get("active") == 0:
                return {"state": "skipped", "downloaded": 0, "message": "订阅已停用"}
            downloaded = await polling.process_subscription(
                sub, strict=True, primary_feed=primary_feed, backup_feed=backup_feed)
            return {"state": "completed", "downloaded": downloaded, "message": "首次轮询完成"}


async def _run_loop(interval_sec: int):
    logger.info("RSS 下载器启动: interval_min=%d", interval_sec // 60)
    while _worker_running:
        try:
            await _poll_subscriptions()
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("RSS 下载器轮询异常")
        await asyncio.sleep(interval_sec)


async def _poll_subscriptions():
    async with _worker_lock:
        with operation_context(new_operation_id("rss")):
            await _poll_subscriptions_with_context()


async def _poll_subscriptions_with_context():
    _worker_status["running"] = True
    _worker_status["errors"] = []
    try:
        subs = list_subscriptions()
        if not subs:
            logger.info("RSS 轮询结束: 无订阅")
            return

        logger.info("RSS 轮询开始: subscriptions=%d", len(subs))
        for sub in subs:
            try:
                downloaded = await polling.process_subscription(sub)
                _worker_status["downloaded"] += downloaded
            except Exception as e:
                msg = f"{sub.get('name', '?')}: {e}"
                _worker_status["errors"].append(msg)
                logger.exception("RSS 订阅处理失败: bangumi_id=%s", sub.get("bangumi_id"))
        _worker_status["last_run"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        logger.info("RSS 轮询完成: downloaded=%d errors=%d",
                    _worker_status["downloaded"], len(_worker_status["errors"]))
    finally:
        _worker_status["running"] = False
