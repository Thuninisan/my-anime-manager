"""Subscription NFO refresh jobs; keep the last result per subscription on disk."""

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import tempfile
from uuid import uuid4

from fastapi import HTTPException

from backend import config, data
from ...db.download_episode_mapping import episode_mapping
from ...utils.paths import USER_DATA_DIR
from .nfo.generator import batch_nfo_generator
from .context import MetadataContext

logger = logging.getLogger(__name__)
ACTIVE = {"queued", "running"}
_directory = USER_DATA_DIR / "nfo_refresh"
_latest: dict[int, dict] = {}
_workers: dict[int, asyncio.Task] = {}
# Serialize refreshes across subscriptions sharing the same show/season paths.
write_lock = asyncio.Lock()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _save(task):
    task["updated_at"] = _now()
    _directory.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".refresh-", dir=_directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(task, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, _directory / f"{task['bangumi_id']}.json")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def initialize():
    _latest.clear()
    if not _directory.exists():
        return
    for path in _directory.glob("*.json"):
        try:
            task = json.loads(path.read_text(encoding="utf-8"))
            if task["status"] in ACTIVE:
                task.update(status="failed", current_sort=None, message="应用重启，刷新已中断，请重新发起。")
                _save(task)
            _latest[task["bangumi_id"]] = task
        except Exception:
            logger.exception("读取 NFO 刷新任务失败: %s", path)


def latest(bangumi_id: int):
    return deepcopy(_latest.get(bangumi_id))


def get_task(task_id: str):
    task = next((t for t in _latest.values() if t["task_id"] == task_id), None)
    if task is None:
        raise HTTPException(404, "刷新任务不存在或已被新的任务替代")
    return deepcopy(task)


def ensure_idle(bangumi_id: int):
    if _latest.get(bangumi_id, {}).get("status") in ACTIVE:
        raise HTTPException(409, "当前订阅正在刷新全部 NFO，请等待完成")


def start(bangumi_id: int):
    previous = _latest.get(bangumi_id)
    if previous and previous["status"] in ACTIVE:
        return deepcopy(previous)
    subscription = next((s for s in data.list_subscriptions() if s["bangumi_id"] == bangumi_id), None)
    if subscription is None:
        raise HTTPException(404, "订阅不存在")
    episodes = deepcopy(data.get_all_episodes(bangumi_id))
    if not episodes:
        raise HTTPException(409, "暂无可刷新的下载记录")
    task = {"task_id": uuid4().hex, "bangumi_id": bangumi_id, "status": "queued",
            "total": len(episodes), "processed": 0, "current_sort": None,
            "succeeded": 0, "skipped": 0, "failed": 0,
            "counts": {"show": 0, "season": 0, "episode": 0},
            "results": [], "message": "等待刷新", "created_at": _now()}
    _save(task)
    _latest[bangumi_id] = task
    base = config.RSS_DOWNLOAD_PATH or config.QBITTORRENT_SAVE_PATH
    worker = asyncio.create_task(_run(task, deepcopy(subscription), episodes, base, config.RSS_PATH_TEMPLATE))
    _workers[bangumi_id] = worker
    worker.add_done_callback(lambda done: _finished(bangumi_id, done))
    return deepcopy(task)


def _finished(bangumi_id, worker):
    if _workers.get(bangumi_id) is worker:
        _workers.pop(bangumi_id, None)
    if not worker.cancelled() and worker.exception() is not None:
        logger.error("NFO 刷新任务异常退出", exc_info=worker.exception())


async def _run(task, subscription, episodes, base, template):
    context = MetadataContext()
    try:
        async with write_lock:
            task.update(status="running", message="正在刷新")
            _save(task)
            for key, record in sorted(episodes.items(), key=lambda item: float(item[0])):
                sort = int(key)
                task["current_sort"] = sort
                _save(task)
                files = []
                warning = "" if record.get("download_path") else "使用当前模板推导路径"
                try:
                    stored = record.get("mapping")
                    if not stored or not any(stored.get(f"{p}_series_id") for p in ("tmdb", "tvdb")):
                        task["skipped"] += 1
                        task["results"].append({"kind": "episode", "sort": sort, "status": "skipped",
                                                "message": "下载记录缺少作品映射", "path": ""})
                        continue
                    mapping = episode_mapping(task["bangumi_id"], sort, stored)
                    if not any(mapping[p]["season_number"] is not None and
                               mapping[p]["episode_number"] is not None and
                               int(mapping[p]["episode_number"]) == mapping[p]["episode_number"]
                               for p in ("tmdb", "tvdb")):
                        task["skipped"] += 1
                        task["results"].append({"kind": "episode", "sort": sort, "status": "skipped",
                                                "message": "缺少有效的季集映射", "path": ""})
                        continue
                    media = record.get("download_path")
                    if media and (not Path(media).is_absolute() or ".." in Path(media).parts):
                        raise ValueError("记录的媒体路径无效，无法安全确定 NFO 输出位置")
                    if not media and not base:
                        raise ValueError("未配置下载目录，无法推导 NFO 输出位置")
                    await asyncio.wait_for(batch_nfo_generator(
                        str(Path(media).anchor) if media else str(Path(base).absolute()),
                        [{"episode_mapping": mapping, "download_path": media}],
                        series_name=subscription.get("series_name") or subscription.get("name", ""),
                        overwrite=True, overwrite_assets=True, download_images=False,
                        metadata_ctx=context, refresh_results=files, path_template=template,
                    ), timeout=300)
                    if any(f["kind"] == "episode" and f["status"] == "success" for f in files):
                        task["succeeded"] += 1
                    else:
                        task["failed"] += 1
                except asyncio.CancelledError:
                    task["failed"] += 1
                    files.append({"kind": "episode", "path": record.get("download_path") or "",
                                  "status": "failed", "message": "应用关闭，本集刷新已中断"})
                    raise
                except Exception as exc:
                    task["failed"] += 1
                    files.append({"kind": "episode", "path": record.get("download_path") or "",
                                  "status": "failed", "message": str(exc) or "刷新超时，请重试"})
                    logger.exception("NFO 刷新失败: bangumi=%s sort=%s", task["bangumi_id"], sort)
                finally:
                    for result in files:
                        result.update(sort=sort, warning=warning)
                        if result["status"] == "success":
                            task["counts"][result["kind"]] += 1
                    task["results"].extend(files)
                    task["processed"] += 1
                    _save(task)
            problems = task["skipped"] or any(r["status"] == "failed" for r in task["results"])
            task["status"] = ("partial_failed" if any(task["counts"].values()) else "failed") if problems else "completed"
            task.update(current_sort=None, message="刷新完成" if not problems else "刷新结束，部分记录未完成，请查看明细")
            _save(task)
            logger.info("NFO 刷新完成: task=%s status=%s counts=%s", task["task_id"], task["status"], task["counts"])
    except asyncio.CancelledError:
        task.update(status="failed", current_sort=None, message="应用关闭，刷新已中断，请重新发起。")
        _save(task)
        raise
    except Exception:
        logger.exception("NFO 批量刷新任务失败: %s", task["task_id"])
        task.update(status="failed", current_sort=None, message="刷新任务失败，请重试并查看日志。")
        _save(task)


async def stop():
    workers = list(_workers.values())
    for worker in workers:
        worker.cancel()
    if workers:
        await asyncio.gather(*workers, return_exceptions=True)
