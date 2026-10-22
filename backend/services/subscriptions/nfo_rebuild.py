"""映射变更后的历史坐标重算与 NFO 重建，不重命名媒体。"""

import asyncio
import hashlib
import logging
import os
from pathlib import Path
import tempfile
from collections import Counter

from ... import config, data
from ...db import mapping_changes as store
from ...db.download_episode_mapping import episode_mapping
from ...domain.rss_episode import rss_episode_ref
from ..metadata.context import MetadataContext
from ..metadata.nfo.generator import batch_nfo_generator
from ..metadata.refresh import write_lock
from .cleanup import ownership, shared, nfo_conflict
from .episode_mapping import subscription_episode_mapping
from .inference import _compute_tmdb_ep_offset, _compute_tvdb_ep_offset

logger = logging.getLogger(__name__)


async def rebuild_for_mapping(task, persist):
    """按当前订阅映射重新解析历史单集，先生成临时 NFO 再替换实际文件。"""
    bid = task["bangumi_id"]
    sub = next((item for item in data.list_subscriptions() if item["bangumi_id"] == bid), None)
    if sub is None:
        raise ValueError("订阅不存在")
    history = data.get_all_episodes(bid)
    path_counts = Counter(row.get("download_path") for row in history.values() if row.get("download_path"))
    task.update(total=len(history), processed=0, succeeded=0, failed=0, skipped=0)
    persist(task)
    context = MetadataContext()
    protected, _ = ownership(bid)
    offsets = {}
    for provider, calculate in (("tmdb", _compute_tmdb_ep_offset), ("tvdb", _compute_tvdb_ep_offset)):
        if provider not in task["providers"]:
            continue
        settings = sub.get(provider) or {}
        if settings.get("id") and settings.get("season") is not None and settings.get("ep_offset") is None:
            value = await calculate(bid, settings["id"], settings["season"], lambda _message: None, context)
            if value is not None:
                offsets[f"{provider}_ep_offset"] = value
                settings["ep_offset"] = value
    async with write_lock:
        with tempfile.TemporaryDirectory(prefix="mam-nfo-rebuild-") as temporary:
            stage_root = Path(temporary)
            for key, record in sorted(history.items(), key=lambda item: float(item[0])):
                sort = int(key)
                task["current_sort"] = sort
                persist(task)
                try:
                    media = Path(record.get("download_path") or "")
                    if not media.is_absolute() or ".." in media.parts or len(media.parts) < 4:
                        raise ValueError("下载记录没有可用的实际媒体文件路径")
                    if path_counts[str(media)] > 1:
                        raise ValueError("多条下载记录共用同一媒体路径，无法确定单集 NFO")
                    if media.exists() and not media.is_file():
                        raise ValueError("记录的媒体路径不是普通文件")
                    if any(path.is_symlink() for path in (media, *media.parents)):
                        raise ValueError("媒体路径包含符号链接")
                    providers = ("tmdb", "tvdb")
                    bound = [p for p in task["providers"] if (sub.get(p) or {}).get("id")]
                    if any((sub.get(p) or {}).get("ep_offset") is None for p in bound):
                        raise ValueError("平台集数偏移尚未确定，保留旧 NFO")
                    if shared(media.with_suffix(".nfo"), protected):
                        raise ValueError("媒体文件被其他记录共用，保留旧 NFO")
                    mapping = await subscription_episode_mapping(
                        rss_episode_ref({}, ""), sub, bid, context, sort=sort, overrides=None)
                    old = record.get("mapping") or {}
                    previous = episode_mapping(bid, sort, old)
                    for provider in providers:
                        if provider not in task["providers"]:
                            mapping[provider] = previous[provider]
                    if not any(mapping[p]["series_id"] for p in providers):
                        raise ValueError("没有可用于生成 NFO 的平台映射")
                    if any(nfo_conflict(bid, path, mapping) for path in (
                            media.parent / "season.nfo", media.parent.parent / "tvshow.nfo")):
                        raise ValueError("共享剧集或季 NFO 的平台映射存在冲突，保留旧文件")
                    for provider in task["providers"]:
                        settings = sub.get(provider) or {}
                        if settings.get("id") and settings.get("ep_offset") is not None and mapping[provider]["episode_id"] is None:
                            raise ValueError(f"{provider.upper()} 第 {sort} 集未匹配到平台目录，保留旧 NFO")
                    if old.get("bangumi_episode_id") is not None:
                        mapping["bangumi"]["episode_id"] = old["bangumi_episode_id"]
                        mapping["bangumi"]["episode_number"] = old.get("bangumi_episode_number")
                    show_key = hashlib.sha256(str(media.parent.parent).encode()).hexdigest()[:16]
                    season_key = hashlib.sha256(str(media.parent).encode()).hexdigest()[:16]
                    staged_media = stage_root / show_key / season_key / media.name
                    staged_media.parent.mkdir(parents=True, exist_ok=True)
                    results = []
                    await asyncio.wait_for(batch_nfo_generator(
                        str(stage_root), [{"episode_mapping": mapping, "download_path": str(staged_media)}],
                        series_name=sub.get("series_name") or sub.get("name", ""),
                        overwrite=True, overwrite_assets=True, download_images=False,
                        metadata_ctx=context, refresh_results=results,
                        path_template=config.RSS_PATH_TEMPLATE), timeout=300)
                    if any(item["status"] == "failed" for item in results):
                        raise ValueError("NFO 生成未完成，保留旧文件")
                    replacements = [(staged_media.with_suffix(".nfo"), media.with_suffix(".nfo")),
                                    (staged_media.parent / "season.nfo", media.parent / "season.nfo"),
                                    (staged_media.parent.parent / "tvshow.nfo", media.parent.parent / "tvshow.nfo")]
                    if any(not source.is_file() for source, _ in replacements):
                        raise ValueError("NFO 临时文件缺失，保留旧文件")
                    current_paths, _ = ownership(bid)
                    if shared(media.with_suffix(".nfo"), current_paths) or any(
                            nfo_conflict(bid, target, mapping) for _, target in replacements[1:]):
                        raise ValueError("NFO 在生成期间出现共享归属冲突，保留旧文件")
                    # 对目标目录中的每份 NFO 原子替换；旧文件在替换时被移除。
                    for source, target in replacements:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        fd, temporary_path = tempfile.mkstemp(prefix=".nfo-rebuild-", dir=target.parent)
                        try:
                            with os.fdopen(fd, "wb") as handle:
                                handle.write(source.read_bytes())
                                handle.flush()
                                os.fsync(handle.fileno())
                            os.replace(temporary_path, target)
                        finally:
                            if os.path.exists(temporary_path):
                                os.unlink(temporary_path)
                    store.save_rebuilt_episode(task, sort, mapping, offsets)
                    data.invalidate_mapping_cache()
                    task["succeeded"] += 1
                    task["results"].append({"sort": sort, "status": "success", "path": str(media.with_suffix(".nfo")), "message": "已按新映射生成"})
                except Exception as exc:
                    task["failed"] += 1
                    task["results"].append({"sort": sort, "status": "failed", "path": record.get("download_path") or "", "message": str(exc)})
                    logger.exception("映射变更后的 NFO 重建失败：bangumi=%s sort=%s", bid, sort)
                finally:
                    task["processed"] += 1
                    task["current_sort"] = None
                    persist(task)
