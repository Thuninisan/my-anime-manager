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
from .cleanup import IMAGES, ownership, safe_path, shared, nfo_conflict
from .episode_mapping import subscription_episode_mapping
from .inference import _compute_tmdb_ep_offset, _compute_tvdb_ep_offset

logger = logging.getLogger(__name__)


def _thumbnails(media):
    if not media.parent.is_dir():
        return []
    return [path for path in media.parent.iterdir()
            if path.stem == media.stem + "-thumb" and path.suffix.lower() in IMAGES]


def _write_atomic(path, content, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".metadata-rebuild-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _install_episode_assets(replacements, removals, commit):
    """先备份本集相关文件；替换、清理或数据库保存失败时回滚文件。"""
    backups = {}
    for path in {target for _, target in replacements} | set(removals):
        safe_path(path)
        if path.exists() and not path.is_file():
            raise ValueError(f"元数据目标不是普通文件：{path}")
        backups[path] = (path.read_bytes(), path.stat().st_mode & 0o777) if path.exists() else None
    installed = []
    try:
        # 调用方把新缩略图排在 NFO 之前，使 NFO 引用的图片先落盘。
        for source, target in replacements:
            _write_atomic(target, source.read_bytes(), backups[target][1] if backups[target] else 0o644)
            installed.append(target)
        for target in removals:
            if target.exists():
                target.unlink()
                installed.append(target)
        commit()
    except Exception as exc:
        failures = []
        for target in reversed(installed):
            try:
                previous = backups[target]
                if previous is None:
                    target.unlink(missing_ok=True)
                else:
                    _write_atomic(target, *previous)
            except Exception:
                failures.append(str(target))
                logger.exception("NFO 与缩略图回滚失败：%s", target)
        if failures:
            raise RuntimeError(f"重建失败且部分文件回滚失败：{'、'.join(failures)}") from exc
        raise


async def rebuild_for_mapping(task, persist):
    """按当前订阅映射重新解析历史单集，先生成临时 NFO 再替换实际文件。"""
    bid = task["bangumi_id"]
    sub = next((item for item in data.list_subscriptions() if item["bangumi_id"] == bid), None)
    if sub is None:
        raise ValueError("订阅不存在")
    history = data.get_all_episodes(bid)
    path_counts = Counter(row.get("download_path") for row in history.values() if row.get("download_path"))
    task.update(total=len(history), processed=0, succeeded=0, failed=0, skipped=0,
                thumbnails={"updated": 0, "missing": 0, "failed": 0})
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
                thumbnail_status = "not_attempted"
                thumbnail_message = ""
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
                    thumbnail_results = []
                    thumbnail_status = "pending"
                    await asyncio.wait_for(batch_nfo_generator(
                        str(stage_root), [{"episode_mapping": mapping, "download_path": str(staged_media)}],
                        series_name=sub.get("series_name") or sub.get("name", ""),
                        overwrite=True, overwrite_assets=True, download_images=False,
                        download_episode_images=True, thumbnail_results=thumbnail_results,
                        metadata_ctx=context, refresh_results=results,
                        path_template=config.RSS_PATH_TEMPLATE), timeout=300)
                    if len(thumbnail_results) != 1:
                        raise ValueError("单集缩略图处理结果缺失，保留旧文件")
                    thumbnail = thumbnail_results[0]
                    thumbnail_status = thumbnail["status"]
                    thumbnail_message = thumbnail["message"]
                    if thumbnail_status == "failed":
                        raise ValueError(thumbnail_message)
                    if any(item["status"] == "failed" for item in results):
                        raise ValueError("NFO 生成未完成，保留旧文件")
                    replacements = [(staged_media.with_suffix(".nfo"), media.with_suffix(".nfo")),
                                    (staged_media.parent / "season.nfo", media.parent / "season.nfo"),
                                    (staged_media.parent.parent / "tvshow.nfo", media.parent.parent / "tvshow.nfo")]
                    if any(not source.is_file() for source, _ in replacements):
                        raise ValueError("NFO 临时文件缺失，保留旧文件")
                    new_thumb = None
                    if thumbnail_status == "success":
                        source = Path(thumbnail["path"])
                        if (source.parent != staged_media.parent or source.stem != media.stem + "-thumb"
                                or source.suffix.lower() not in IMAGES or not source.is_file()):
                            raise ValueError("新缩略图的临时文件无效")
                        new_thumb = media.with_name(source.name)
                        replacements.insert(0, (source, new_thumb))
                    old_thumbs = _thumbnails(media)
                    removals = [path for path in old_thumbs if path != new_thumb]
                    current_paths, _ = ownership(bid)
                    targets = [target for _, target in replacements] + removals
                    if any(shared(target, current_paths) for target in targets
                           if target.name not in {"season.nfo", "tvshow.nfo"}) or any(
                            nfo_conflict(bid, target, mapping) for target in targets
                            if target.name in {"season.nfo", "tvshow.nfo"}):
                        raise ValueError("NFO 在生成期间出现共享归属冲突，保留旧文件")
                    _install_episode_assets(replacements, removals,
                        lambda: store.save_rebuilt_episode(task, sort, mapping, offsets))
                    data.invalidate_mapping_cache()
                    task["succeeded"] += 1
                    task["thumbnails"]["updated" if new_thumb else "missing"] += 1
                    message = "NFO 和单集缩略图已更新" if new_thumb else "NFO 已更新，新映射无缩略图，已清理旧图及引用"
                    task["results"].append({"sort": sort, "status": "success", "path": str(media.with_suffix(".nfo")),
                                            "message": message, "thumbnail_status": thumbnail_status,
                                            "thumbnail_path": str(new_thumb) if new_thumb else ""})
                except Exception as exc:
                    task["failed"] += 1
                    if thumbnail_status != "not_attempted":
                        thumbnail_status = "failed"
                        task["thumbnails"]["failed"] += 1
                    task["results"].append({"sort": sort, "status": "failed", "path": record.get("download_path") or "",
                                            "message": str(exc) or "NFO 与缩略图重建超时", "thumbnail_status": thumbnail_status})
                    logger.exception("映射变更后的 NFO 与缩略图重建失败：bangumi=%s sort=%s", bid, sort)
                finally:
                    task["processed"] += 1
                    task["current_sort"] = None
                    persist(task)
