"""Server-owned enrichment and first poll for newly created RSS subscriptions."""

import asyncio
import logging
from datetime import datetime

from .. import data
from . import downloader, rss as rss_service

logger = logging.getLogger(__name__)

_jobs: dict[int, dict] = {}
_tasks: dict[int, asyncio.Task] = {}


def get_status(bangumi_id: int) -> dict | None:
    job = _jobs.get(bangumi_id)
    return dict(job) if job else None


def start(bangumi_id: int) -> None:
    """Start once after the new subscription has been saved."""
    if bangumi_id in _tasks and not _tasks[bangumi_id].done():
        return
    _jobs[bangumi_id] = {"state": "enriching", "message": "正在获取订阅元数据", "downloaded": 0, "finished_at": ""}
    task = asyncio.create_task(_run(bangumi_id))
    _tasks[bangumi_id] = task
    task.add_done_callback(lambda done: _tasks.pop(bangumi_id, None) if _tasks.get(bangumi_id) is done else None)


async def stop() -> None:
    tasks = list(_tasks.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _tasks.clear()


async def _run(bangumi_id: int) -> None:
    job = _jobs[bangumi_id]
    try:
        sub = next((item for item in data.list_subscriptions() if item["bangumi_id"] == bangumi_id), None)
        if sub is None:
            raise ValueError("订阅已删除")
        primary = sub.get("primary", {}).get("rss_url", "")
        backup = sub.get("backup", {}).get("rss_url", "")
        snapshots: dict[str, dict] = {}
        for url in dict.fromkeys(url for url in (primary, backup) if url):
            try:
                snapshots[url] = await rss_service.fetch_rss_snapshot(url)
            except Exception:
                logger.warning("RSS 获取失败: %s", url, exc_info=True)
                snapshots[url] = {"items": []}
        primary_feed = snapshots.get(primary)
        backup_feed = snapshots.get(backup)
        result = await downloader.enrich_subscription(
            bangumi_id, primary_rss_url=primary, backup_rss_url=backup,
            primary_feed=primary_feed, backup_feed=backup_feed,
            on_progress=lambda message: job.update(message=message),
        )
        if not result:
            job.update(state="skipped", message="元数据获取失败，将在后续轮询重试")
            return
        primary_offset = result.pop("primary_offset", None)
        backup_offset = result.pop("backup_offset", None)
        if not data.update_subscription(bangumi_id, result):
            raise ValueError("订阅已删除")
        if primary_offset is not None:
            data.set_subscription_rss_offset(bangumi_id, "primary", primary_offset)
        if backup_offset is not None:
            data.set_subscription_rss_offset(bangumi_id, "backup", backup_offset)

        if ((primary and primary_offset is None) or (backup and backup_offset is None)
                or not (primary or backup)):
            job.update(state="skipped", message="无法确定 RSS 集数偏移量，将在后续轮询重试")
            return
        if not ((result.get("tmdb") or {}).get("id") or (result.get("tvdb") or {}).get("id")
                or data.get_tmdb_id(bangumi_id) or data.get_tvdb_id(bangumi_id)):
            job.update(state="skipped", message="缺少 TMDB/TVDB ID，设置后可重新轮询")
            return

        job.update(state="polling", message="正在轮询新订阅")
        job.update(await downloader.poll_subscription(
            bangumi_id, primary_feed=primary_feed, backup_feed=backup_feed))
    except asyncio.CancelledError:
        job.update(state="skipped", message="服务已停止，等待后续轮询")
        raise
    except Exception as exc:
        logger.exception("RSS 首次轮询失败: bangumi_id=%s", bangumi_id)
        job.update(state="failed", message=f"首次轮询失败: {exc}")
    finally:
        job["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
