"""Manage Torrent processing tasks and resume unfinished checkpoint stages."""

# 职责边界：本文件独占进程内 Torrent 任务注册表，负责按哈希去重、启动/取消任务，
# 使用既有租约执行后处理阶段，并从 SQLite 检查点恢复未结束的流程。
# submission 调用 start_monitor()；应用生命周期调用 run_scheduler()/stop()；
# 设置变更可调用 recover_pending()。调度循环的 Task 由应用生命周期持有并先停止。
# 本文件不解析 HTTP 请求、不提交新下载、不搜索或修正映射，不直接实现 NFO、
# 硬链接和字体处理；这些操作委托现有服务，检查点与最终状态事务由 db 层负责。
# 仅管理 torrent_processing 流程，不承担 RSS 下载器或目录扫描任务；不导入 API。

import asyncio
import logging

from ... import config
from ...clients.qbittorrent import get_torrents_by_hashes, login as qb_login
from ...db import recovery as recovery_store
from ...db import torrents as torrent_store
from ...logging.logging_config import operation_context
from .. import bd_replacement
from ..workflow_recovery import execution
from . import fontinass
from .monitor import monitor_download, monitor_processing

logger = logging.getLogger(__name__)
_download_tasks: dict[str, asyncio.Task] = {}


async def _run_work(record: dict, context: dict | None, job: recovery_store.RecoveryJob) -> None:
    """Advance an owned Torrent checkpoint through its existing processing stages."""
    info_hash = record["info_hash"]
    processing = job.checkpoint["processing"]
    if job.stage in {"wait_download", "organize_files"}:
        job.save(stage="organize_files")
        if context is None:
            linked = await monitor_processing(info_hash, record["torrent_name"], processing)
        else:
            linked = await monitor_download(
                info_hash=info_hash,
                torrent_name=context["torrent_name"],
                files=context["files"],
                uploaded_subtitles=context["uploaded_subtitles"],
                hardlink_root=context["hardlink_root"],
                series_name=context["series_name"],
                skip_nfo=context["skip_nfo"],
                movie_meta=context["movie_meta"],
            )
        required = sum(f["action"] == "hardlink" for f in processing["files"])
        success = linked is not None and len(linked) == required
        if success and processing.get("replace_bangumi_id") is not None:
            bangumi_id = processing["replace_bangumi_id"]
            mapping = [
                {"episode_mapping": item["episode_mapping_snapshot"]["episode_mapping"], "is_subtitle": False}
                for item in processing["files"] if item["action"] == "hardlink"
            ]
            episodes = bd_replacement.validate_mapping(bangumi_id, mapping)
            client = await qb_login(config.QBITTORRENT_URL, config.QBITTORRENT_USERNAME, config.QBITTORRENT_PASSWORD)
            hashes, old_links = await bd_replacement.old_files(client, bangumi_id, episodes, info_hash)
            success = await bd_replacement.finalize(client, bangumi_id, episodes, hashes, old_links, linked, required)
        job.checkpoint["media_status"] = "completed" if success else "failed"
        job.save(stage="fontinass" if linked is not None else "finalize")

    if job.stage == "fontinass":
        # Font processing does not hold back the completed media result.
        torrent_store.finish_torrent(info_hash, job.checkpoint["media_status"],
                                    job=job, keep_recovery=True)
        if not await fontinass.process(info_hash):
            job.release(blocked=True)
            return
        job.checkpoint.pop("fontinass", None)
        job.save(stage="finalize")

    if job.stage != "finalize":
        raise ValueError("unsupported_torrent_recovery_stage")
    torrent_store.finish_torrent(info_hash, job.checkpoint["media_status"], job=job)


def start_monitor(record: dict, context: dict | None = None) -> None:
    """Dispatch an owned workflow unless this hash already has an active task."""
    info_hash = record["info_hash"]
    existing = _download_tasks.get(info_hash)
    if existing and not existing.done():
        return

    async def run() -> None:
        job = None
        try:
            job = recovery_store.RecoveryJob.claim("torrent_processing", info_hash)
            if job is None:
                return
            with operation_context(job.id):
                logger.info("workflow.start torrent=%s stage=%s mode=%s", info_hash,
                            job.stage, "recovery" if context is None else "normal")
                async with execution(job):
                    await _run_work(record, context, job)
        except recovery_store.LostLease:
            logger.exception("Torrent 恢复任务失去执行租约: %s", info_hash)
        except asyncio.CancelledError:
            if job is not None:
                try:
                    job.release()
                except recovery_store.LostLease:
                    pass
            raise
        except Exception:
            logger.exception("Torrent 后处理异常，保留恢复检查点: %s", info_hash)
            if job is not None:
                try:
                    job.release(delay_seconds=60)
                except recovery_store.LostLease:
                    pass
        finally:
            _download_tasks.pop(info_hash, None)

    _download_tasks[info_hash] = asyncio.create_task(run())


async def recover_pending() -> None:
    """Resume each unfinished workflow from its shared checkpoint stage."""
    pending = torrent_store.list_pending_torrents()
    download_jobs = []
    for record in pending:
        info_hash = record["info_hash"]
        if info_hash in _download_tasks:
            continue
        with recovery_store.new_session() as session:
            row = recovery_store.read(session, "torrent_processing", info_hash)
            stage = row.stage if row else None
            blocked = row is not None and row.state == "blocked"
        if blocked and not config.FONTINASS_ENABLED:
            continue
        if stage in {"fontinass", "finalize"}:
            start_monitor(record)
        else:
            download_jobs.append(record)
    if not download_jobs:
        return
    try:
        client = await qb_login(config.QBITTORRENT_URL, config.QBITTORRENT_USERNAME, config.QBITTORRENT_PASSWORD)
        torrents = await get_torrents_by_hashes(client, [record["info_hash"] for record in download_jobs], raise_on_error=True)
    except Exception:
        logger.exception("Torrent 监控恢复延迟：qBittorrent 不可用")
        return
    for record in download_jobs:
        info_hash = record["info_hash"]
        if info_hash in _download_tasks:
            continue
        if info_hash in torrents:
            start_monitor(record)
        else:
            job = recovery_store.RecoveryJob.claim("torrent_processing", info_hash)
            if job is not None:
                logger.warning("Torrent 已从 qBittorrent 移除，结束恢复任务: %s", info_hash)
                torrent_store.finish_torrent(info_hash, "failed", job=job)


async def run_scheduler() -> None:
    """Check unfinished workflows every 60 seconds; the caller owns this task."""
    while True:
        try:
            await recover_pending()
        except Exception:
            logger.exception("恢复任务调度失败，将在下一轮重试")
        await asyncio.sleep(60)


async def stop() -> None:
    """Cancel and await processing tasks after the recovery scheduler is stopped."""
    tasks = list(_download_tasks.items())
    for info_hash, task in tasks:
        if not task.done():
            task.cancel()
            logger.info("Download monitor cancelled: %s", info_hash[:8])
    if tasks:
        await asyncio.gather(*(task for _, task in tasks), return_exceptions=True)
    _download_tasks.clear()
