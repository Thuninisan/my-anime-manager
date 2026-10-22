"""映射修改影响预览，以及已确认的文件清理和 NFO 重建。"""

import asyncio
import logging
from datetime import datetime, timezone
from uuid import uuid4

from ... import data
from ...db import mapping_changes as store
from ...db.mapping_fields import MAPPING_FIELDS, merge_mapping
from ..metadata import refresh as nfo_refresh
from ..subscriptions import cleanup, episode_download, worker, nfo_rebuild

logger = logging.getLogger(__name__)
_workers: dict[str, asyncio.Task] = {}
_locks: dict[int, asyncio.Lock] = {}


def _lock(bangumi_id):
    return _locks.setdefault(bangumi_id, asyncio.Lock())


def _public(task):
    return {key: value for key, value in task.items() if key not in {"changes", "expected", "expires_at", "mapping_snapshot"}}


def get_task(bangumi_id, task_id):
    task = store.get(task_id, bangumi_id)
    return _public(task) if task else None


def latest_task(bangumi_id):
    task = store.latest(bangumi_id)
    return _public(task) if task else None


async def preview(bangumi_id, changes):
    store.ensure_idle(bangumi_id)
    current = store.read_snapshot(bangumi_id)
    if not current["mapping"]:
        raise ValueError("映射不存在")
    old_mikan = current["mapping"].get("mikan_id")
    mikan_changed = "mikan_id" in changes and changes["mikan_id"] != old_mikan
    platform_changed = any(key in changes and current["mapping"].get(key) != value
                           for key, value in changes.items() if key.startswith(("tmdb_", "tvdb_")))
    if mikan_changed and current["subscription"]:
        plan = await cleanup.plan(bangumi_id, current)
        now = datetime.now(timezone.utc)
        task = {"task_id": uuid4().hex, "bangumi_id": bangumi_id, "kind": "cleanup",
                "status": "preview", "created_at": now.isoformat(),
                "expires_at": now.timestamp() + 15 * 60, "expected": store.digest(current),
                "changes": changes, "plan": plan, "subscription_name": current["subscription"]["name"],
                "old_mikan_id": old_mikan, "new_mikan_id": changes["mikan_id"],
                "episode_count": len(current["episodes"]), "results": [], "deletion_started": False,
                "message": "请确认清理旧订阅和关联文件"}
        # 外部读取预览期间也可能新增下载记录。
        if store.digest(store.read_snapshot(bangumi_id)) != task["expected"]:
            raise store.MappingConflict("预览期间订阅发生变化，请重新保存")
        store.put(task)
        return _public(task)
    result = {"kind": "nfo_offer" if platform_changed and not mikan_changed and current["subscription"] else "simple",
            "bangumi_id": bangumi_id, "episode_count": len(current["episodes"]),
            "old_mikan_id": old_mikan, "new_mikan_id": changes.get("mikan_id", old_mikan)}
    if result["kind"] == "nfo_offer":
        now = datetime.now(timezone.utc)
        result["task_id"] = uuid4().hex
        store.put({**result, "status": "preview", "created_at": now.isoformat(),
                   "expires_at": now.timestamp() + 15 * 60,
                   "target_mapping": merge_mapping(current["mapping"], changes),
                   "providers": [p for p in ("tmdb", "tvdb") if any(
                       key in changes and current["mapping"].get(key) != changes[key]
                       for key in (f"{p}_id", f"{p}_season", f"{p}_ep_offset"))]})
    return result


def _schedule(task):
    worker_task = asyncio.create_task(_run(task))
    _workers[task["task_id"]] = worker_task
    def done(finished):
        _workers.pop(task["task_id"], None)
        if not finished.cancelled() and finished.exception():
            logger.error("映射任务异常退出：%s", task["task_id"], exc_info=finished.exception())
    worker_task.add_done_callback(done)


def confirm(bangumi_id, task_id):
    task = store.get(task_id, bangumi_id)
    if not task or task["kind"] != "cleanup":
        raise ValueError("清理预览不存在")
    if task["status"] in {"queued", "running", "completed"}:
        return _public(task)
    store.claim(task)
    _schedule(task)
    return _public(task)


def retry(bangumi_id, task_id):
    task = store.get(task_id, bangumi_id)
    if not task or task["status"] not in {"failed", "partial_failed"}:
        raise ValueError("任务不存在或当前不可重试")
    if task["kind"] == "cleanup":
        store.claim(task, retry=True)
    else:
        store.ensure_idle(bangumi_id)
        snapshot = store.read_snapshot(bangumi_id)
        if snapshot["mapping"] != task.get("mapping_snapshot", snapshot["mapping"]):
            raise store.MappingConflict("映射或下载记录已变化，请重新发起 NFO 重建")
        task["expected"] = store.digest(snapshot)
        store.claim(task, retry=True)
    _schedule(task)
    return _public(task)


def start_nfo(bangumi_id, preview_id):
    previous = store.latest(bangumi_id)
    if previous and previous.get("offer_id") == preview_id:
        return _public(previous)
    store.ensure_idle(bangumi_id)
    snapshot = store.read_snapshot(bangumi_id)
    if snapshot["subscription"] is None:
        raise ValueError("订阅不存在")
    if not snapshot["episodes"]:
        raise ValueError("暂无可重建 NFO 的下载记录")
    offer = store.get(preview_id, bangumi_id)
    if not offer or offer["kind"] != "nfo_offer":
        raise ValueError("NFO 重建确认预览不存在")
    if datetime.now(timezone.utc).timestamp() > offer["expires_at"]:
        raise store.MappingConflict("NFO 重建确认已过期，请重新编辑映射")
    if any(snapshot["mapping"].get(field) != offer["target_mapping"].get(field) for field in MAPPING_FIELDS):
        raise store.MappingConflict("平台映射已发生变化，请重新确认")
    task = {"task_id": uuid4().hex, "bangumi_id": bangumi_id, "kind": "nfo",
            "status": "queued", "created_at": datetime.now(timezone.utc).isoformat(),
            "expected": store.digest(snapshot), "results": [], "message": "等待重建",
            "mapping_snapshot": snapshot["mapping"],
            "providers": offer["providers"],
            "offer_id": preview_id,
            "total": len(snapshot["episodes"]), "processed": 0}
    store.start_task(task)
    _schedule(task)
    return _public(task)


async def _run(task):
    bid = task["bangumi_id"]
    async with _lock(bid):
        try:
            async with worker.maintenance():
                await asyncio.wait_for(episode_download.wait_for_subscription(bid), timeout=300)
                nfo_refresh.ensure_idle(bid)
                store.verify(task)
                task.update(status="running", message="正在清理旧订阅" if task["kind"] == "cleanup" else "正在重建 NFO 和单集缩略图")
                store.put(task)
                if task["kind"] == "cleanup":
                    async with nfo_refresh.write_lock:
                        await cleanup.execute(task, store.put)
                        store.finish_cleanup(task)
                    data.invalidate_mapping_cache()
                else:
                    await nfo_rebuild.rebuild_for_mapping(task, store.put)
                    task.update(status="completed" if task["failed"] == 0 else "partial_failed",
                                message="NFO 和单集缩略图重建完成" if task["failed"] == 0 else "部分 NFO 或缩略图重建失败，请查看明细")
                    task["mapping_snapshot"] = store.read_snapshot(bid)["mapping"]
                    store.put(task)
        except asyncio.CancelledError:
            if task["kind"] == "nfo":
                task["mapping_snapshot"] = store.read_snapshot(bid)["mapping"]
            task.update(status="failed", message="任务因应用停止而中断，可重试")
            store.put(task)
            raise
        except Exception as exc:
            logger.exception("映射变更任务失败：bangumi=%s", bid)
            if task["kind"] == "nfo":
                task["mapping_snapshot"] = store.read_snapshot(bid)["mapping"]
            task.update(status="failed", message=str(exc) or "等待已有任务超时，请稍后重试")
            store.put(task)


async def stop():
    tasks = list(_workers.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
