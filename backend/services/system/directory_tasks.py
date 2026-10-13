"""Directory scan/watch scheduling and state, independent of HTTP routes."""

import asyncio
import logging
from typing import Optional
from pathlib import Path

from ..torrent.batch_service import process_torrent
from ...logging.logging_config import new_operation_id, operation_context

logger = logging.getLogger(__name__)

# ── Scan ──
_scan_task: Optional[asyncio.Task] = None
_scan_status: dict = {
    "running": False,
    "dir": "",
    "total": 0,
    "processed": 0,
    "deleted": 0,
    "failed": 0,
    "current_file": "",
    "errors": [],
}

# ── Watch ──
_watch_task: Optional[asyncio.Task] = None
_watch_status: dict = {
    "running": False,
    "dir": "",
    "processed": 0,
    "failed": 0,
    "errors": [],
}


class ScanInProgress(RuntimeError):
    pass


def start_scan(dir_path: str) -> None:
    global _scan_task
    if _scan_task and not _scan_task.done():
        raise ScanInProgress("扫描任务已在运行中")
    _scan_task = asyncio.create_task(_scan_worker(dir_path))


def start_watch(dir_path: str) -> None:
    global _watch_task
    if _watch_task and not _watch_task.done():
        return
    _watch_task = asyncio.create_task(_watch_worker(dir_path))


def scan_status() -> dict:
    return {**_scan_status, "errors": list(_scan_status["errors"])}


def watch_status() -> dict:
    return {**_watch_status, "errors": list(_watch_status["errors"])}


async def stop() -> None:
    global _scan_task, _watch_task
    tasks = [task for task in (_scan_task, _watch_task) if task is not None]
    for task in tasks:
        if not task.done():
            task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _scan_task = _watch_task = None
    _scan_status["running"] = _watch_status["running"] = False


async def _scan_worker(dir_path: str):
    """Background worker: scan directory and process each .torrent file."""
    with operation_context(new_operation_id("scan")):
        await _scan_worker_with_context(dir_path)


async def _scan_worker_with_context(dir_path: str):
    p = Path(dir_path)
    if not p.exists():
        _scan_status.update(running=False, errors=["目录不存在: " + dir_path])
        return
    torrents = sorted(p.glob("*.torrent"))
    _scan_status.update(running=True, dir=dir_path, total=len(torrents),
                               processed=0, deleted=0, failed=0, current_file="", errors=[])
    for tf in torrents:
        _scan_status["current_file"] = tf.name
        try:
            ok = await process_torrent(str(tf))
            if ok:
                tf.unlink()
                _scan_status["deleted"] += 1
            else:
                _scan_status["failed"] += 1
        except Exception as exc:
            _scan_status["failed"] += 1
            _scan_status["errors"].append(f"{tf.name}: {exc}")
            logger.exception("目录扫描处理失败: %s", tf.name)
        _scan_status["processed"] += 1
    _scan_status["running"] = False


async def _watch_worker(dir_path: str):
    """Background loop: watch directory for .torrent files every 30s."""
    with operation_context(new_operation_id("watch")):
        await _watch_worker_with_context(dir_path)


async def _watch_worker_with_context(dir_path: str):
    p = Path(dir_path)
    if not p.exists():
        p.mkdir(parents=True, exist_ok=True)
    _watch_status.update(running=True, dir=dir_path, processed=0, failed=0, errors=[])
    while True:
        try:
            torrents = sorted(p.glob("*.torrent"))
            for tf in torrents:
                _watch_status["current_file"] = tf.name
                try:
                    ok = await process_torrent(str(tf))
                    if ok:
                        tf.unlink()
                    else:
                        failed_dir = p / "failed"
                        failed_dir.mkdir(exist_ok=True)
                        tf.rename(failed_dir / tf.name)
                        _watch_status["failed"] += 1
                except Exception as exc:
                    _watch_status["failed"] += 1
                    _watch_status["errors"].append(f"{tf.name}: {exc}")
                    logger.exception("目录监控处理失败: %s", tf.name)
                _watch_status["processed"] += 1
        except Exception:
            logger.exception("Watch worker error")
        await asyncio.sleep(30)
