"""订阅单集下载编排：业务判断、映射和路径、替换、后台 NFO 与历史记录。

RSS 和人工上传共用单集提交入口，下载执行不依赖 NFO 生成结果。
"""

import asyncio
import logging
from contextlib import asynccontextmanager
import tempfile
from pathlib import Path

import httpx
from fastapi import HTTPException

from ... import config
from ...clients.qbittorrent import login as qb_login, delete_torrent, get_torrents_by_hashes
from ...data import (
    list_subscriptions, get_all_episodes,
    mark_downloaded, reset_fail_count, increment_fail_count, MAX_FAIL_COUNT,
)
from ...logging.logging_config import safe_url
from .inference import enrich_subscription
from ..metadata.context import MetadataContext
from ..downloads.episode_submission import DownloadTarget, VIDEO_EXTENSIONS, submit_torrent
from ..media.paths import format_download_path
from ..metadata.nfo.generator import batch_nfo_generator
from ..metadata.refresh import write_lock
from ...utils.torrent_file_reader import read_torrent_file_list
from ...utils.torrent_hash import compute_info_hash
from .episode_mapping import subscription_episode_mapping
from .selection import SOURCE_PRIORITY

logger = logging.getLogger(__name__)


async def _download_torrent_file(torrent_url: str, max_retries: int = 3) -> bytes:
    """Download a .torrent file.  Retry is handled by fetch_with_retry.

    The only extra logic beyond fetch_with_retry is detecting HTML error
    pages that are served with 200 OK (some CDNs do this).
    """
    from ...utils.http_retry import fetch_with_retry as _fetch

    resp = await _fetch(torrent_url, timeout=60.0, max_retries=max_retries,
                        label="torrent")

    # Detect HTML error pages served with 200 OK
    content_type = resp.headers.get("content-type", "")
    if "text/html" in content_type and len(resp.content) < 2048:
        raise httpx.HTTPStatusError(
            "Server returned HTML instead of a torrent file (likely an error page)",
            request=resp.request,
            response=resp,
        )

    return resp.content


async def download_item(item: dict, bangumi_id: int, source: str, sub: dict, metadata_ctx: MetadataContext | None = None) -> bool:
    """Submit an RSS candidate, returning whether its history was recorded."""
    metadata_ctx = metadata_ctx or MetadataContext()
    torrent_url = item["torrent_url"]
    guid = item["guid"]
    from ...domain.rss_episode import item_episode_ref
    ref = item_episode_ref(item)
    rss_ep_num = ref["rss_episode_number"]
    if not torrent_url or rss_ep_num is None:
        return False

    logger.debug(f"      ⬇️ EP{rss_ep_num:02d} [{source}] {guid[:60]}...")

    # ── Ensure subscription has enrichment fields ──────────────────
    bgm_season = sub.get("bgm", {}).get("season")
    if bgm_season is None:
        logger.debug(f"         🔗 订阅缺少 bgm_season，正在丰富化...")
        primary_rss = sub.get("primary", {}).get("rss_url", "")
        backup_rss = sub.get("backup", {}).get("rss_url", "")
        enriched = await enrich_subscription(
            bangumi_id,
            primary_rss_url=primary_rss,
            backup_rss_url=backup_rss,
            metadata_ctx=metadata_ctx,
        )
        if enriched:
            # Pop offsets before top-level update; write to nested keys
            primary_offset = enriched.pop("primary_offset", None)
            backup_offset = enriched.pop("backup_offset", None)
            sub.update(enriched)
            from ...data import update_subscription, set_subscription_rss_offset
            update_subscription(bangumi_id, enriched, identity_source=(enriched.get("resource_resolution") or {}).get("reason", "legacy_unknown"))
            current = next((value for value in list_subscriptions() if value["bangumi_id"] == bangumi_id), None)
            if current is not None:
                sub.update(current)
            if primary_offset is not None:
                set_subscription_rss_offset(bangumi_id, "primary", primary_offset)
                sub.setdefault("primary", {})["offset"] = primary_offset
            if backup_offset is not None:
                set_subscription_rss_offset(bangumi_id, "backup", backup_offset)
                sub.setdefault("backup", {})["offset"] = backup_offset
        else:
            logger.error(f"         ❌ 丰富化失败，将在下次轮询重试")
            return False

    from ...domain.resource_adapters import subscription_identity
    identity = subscription_identity(sub, bangumi_id)
    tmdb_id = identity["tmdb_series_id"] or 0
    tvdb_id = identity["tvdb_series_id"] or 0

    # ── Match RSS episode to Bangumi sort ──────────────────────────
    # sort is assigned by selection.fetch_passed_items via rss_ep + rss_offset.
    sort = item.get("sort")
    if sort is None:
        logger.warning(f"         ⚠️ rss_ep={rss_ep_num} 无法映射到 sort，跳过")
        return False
    if sort != rss_ep_num:
        logger.debug(f"         📐 rss_ep={rss_ep_num} → sort={sort}")

    # ⛔ Guard: need at least one metadata source
    if not tmdb_id and not tvdb_id:
        logger.warning("跳过下载: bangumi_id=%s episode=%s 缺少 TMDB/TVDB ID", bangumi_id, rss_ep_num)
        logger.debug(f"         💡 请在订阅卡片中手动设置 TMDB 或 TVDB ID")
        increment_fail_count(bangumi_id, sort)
        return False

    bgm_sortrange = sub.get("bgm", {}).get("sortrange", [0, 0])
    if bgm_sortrange[0] > 0 and (sort < bgm_sortrange[0] or sort > bgm_sortrange[1]):
        logger.warning(f"         ⚠️ sort={sort} 超出 bgm_sortrange={bgm_sortrange}，但仍继续处理")

    # ── Download .torrent ──────────────────────────────────────────
    try:
        torrent_content = await _download_torrent_file(torrent_url)
    except httpx.HTTPStatusError as e:
        status = e.response.status_code if hasattr(e.response, 'status_code') else '?'
        logger.error("下载 .torrent 失败 (HTTP %s): %s", status, safe_url(torrent_url))
        # Track failure count so we can eventually give up on dead URLs
        fail_count = increment_fail_count(bangumi_id, sort)
        if fail_count >= MAX_FAIL_COUNT:
            logger.error(f"      🚫 已连续失败 {fail_count} 次，跳过此集（将不再重试）")
        return False
    except Exception as e:
        exc_name = type(e).__name__
        logger.error("下载 .torrent 失败 [%s]: %s", exc_name, safe_url(torrent_url))
        # Track failure count so we can eventually give up on dead URLs
        fail_count = increment_fail_count(bangumi_id, sort)
        if fail_count >= MAX_FAIL_COUNT:
            logger.error(f"      🚫 已连续失败 {fail_count} 次，跳过此集（将不再重试）")
        return False

    with tempfile.NamedTemporaryFile(suffix=".torrent", delete=False) as f:
        f.write(torrent_content)
        tmp_path = f.name

    try:
        info_hash = await submit_episode(
            tmp_path, bangumi_id, sort, source, sub=sub, guid=guid,
            pub_date=item.get("pub_date", ""),
            metadata_ctx=metadata_ctx, rss_ref=ref, automatic_replace=True,
        )
    except Exception:
        logger.exception("RSS 单集提交失败: bangumi_id=%s sort=%s", bangumi_id, sort)
        return False
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    return bool(info_hash)


_episode_locks: dict[tuple, tuple[asyncio.Lock, int]] = {}
_nfo_tasks: set[asyncio.Task] = set()


async def stop_nfo_tasks() -> None:
    """应用关闭 HTTP 客户端前，取消并等待后台元数据任务。"""
    tasks = tuple(_nfo_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


@asynccontextmanager
async def _episode_lock(key: tuple):
    lock, users = _episode_locks.get(key, (asyncio.Lock(), 0))
    _episode_locks[key] = (lock, users + 1)
    try:
        async with lock:
            yield
    finally:
        _, users = _episode_locks[key]
        if users == 1:
            del _episode_locks[key]
        else:
            _episode_locks[key] = (lock, users - 1)


async def _generate_nfo(bangumi_id, sort, target, mapping, series_name, overwrite,
                        metadata_ctx, path_template):
    """后台元数据生成失败仅记录日志，不影响下载提交。"""
    try:
        async with write_lock:
            summary = await batch_nfo_generator(
                target.save_path,
                [{"episode_mapping": mapping, "download_path": target.media_path}],
                series_name=series_name, overwrite=overwrite,
                metadata_ctx=metadata_ctx, path_template=path_template,
            )
            if not summary.get("episodesProcessed", summary.get("nfoGenerated", 0)):
                logger.error("单集 NFO 未生成: bangumi_id=%s sort=%s", bangumi_id, sort)
    except Exception:
        logger.exception("单集 NFO 生成失败: bangumi_id=%s sort=%s", bangumi_id, sort)


async def submit_episode(
    torrent_path: str, bangumi_id: int, sort: int, source: str, *,
    guid: str, sub: dict | None = None, pub_date: str = "",
    metadata_ctx: MetadataContext | None = None, replace_existing: bool = False,
    rss_ref=None, automatic_replace: bool = False,
) -> str | None:
    """准备单集下载目标，执行下载并独立生成 NFO；RSS 跳过时返回 None。"""
    async with _episode_lock(("submit", bangumi_id, sort)):
        current = next((s for s in list_subscriptions() if s["bangumi_id"] == bangumi_id), None)
        if current is None:
            raise HTTPException(404, "订阅不存在")
        sub = current
        if sort < 0:
            raise HTTPException(400, "集号无效")
        from ...domain.resource_adapters import subscription_identity
        from ...domain.rss_episode import rss_episode_ref
        identity = subscription_identity(sub, bangumi_id)
        if not identity["tmdb_series_id"] and not identity["tvdb_series_id"]:
            raise HTTPException(400, "订阅未关联 TMDB 或 TVDB ID，无法生成 NFO")
        files = read_torrent_file_list(torrent_path)
        videos = [f for f in files if Path(f["name"]).suffix.lower() in VIDEO_EXTENSIONS]
        if len(videos) != 1:
            raise HTTPException(400, f"种子中视频文件数量不为1 (found {len(videos)})")
        torrent_hash = compute_info_hash(torrent_path)
        if not torrent_hash:
            raise HTTPException(400, "无效的 .torrent 文件")
        old = get_all_episodes(bangumi_id).get(str(sort), {})
        if old.get("info_hash", "").lower() == torrent_hash.lower():
            if replace_existing:
                raise HTTPException(400, "新种子与当前种子相同")
            return None if automatic_replace else torrent_hash
        if automatic_replace and old.get("source"):
            new_priority = SOURCE_PRIORITY.get(source, -1)
            old_priority = SOURCE_PRIORITY.get(old["source"], -1)
            replace_existing = new_priority > old_priority or (
                new_priority == old_priority and bool(pub_date and old.get("pub_date"))
                and pub_date > old["pub_date"]
            )
            if not replace_existing:
                return None
        metadata_ctx = metadata_ctx or MetadataContext()
        mapping = await subscription_episode_mapping(
            rss_ref if rss_ref is not None else rss_episode_ref({}, ""),
            sub, bangumi_id, metadata_ctx, sort=sort, overrides=old.get("mapping"),
        )
        template = config.RSS_PATH_TEMPLATE
        rss_base = config.RSS_DOWNLOAD_PATH or config.QBITTORRENT_SAVE_PATH
        path_sub = {
            **sub,
            "tmdb": {**sub.get("tmdb", {}), "season": mapping["tmdb"]["season_number"]},
            "tvdb": {**sub.get("tvdb", {}), "season": mapping["tvdb"]["season_number"]},
        }
        tmdb_title = ""
        if "{tmdb_title" in template and identity["tmdb_series_id"]:
            detail = await metadata_ctx.get_tmdb_detail(identity["tmdb_series_id"], "zh-CN")
            tmdb_title = detail.get("name", "")
        relative = format_download_path(
            template, path_sub, sort=sort,
            bangumi_ep=mapping["bangumi"]["episode_number"],
            tmdb_episode=mapping["tmdb"]["episode_number"],
            tvdb_episode=mapping["tvdb"]["episode_number"],
            tmdb_title=tmdb_title, ext=Path(videos[0]["name"]).suffix,
        ).lstrip("/")
        target = DownloadTarget(rss_base, relative)
        root = Path(rss_base).resolve()
        media = Path(target.media_path).resolve()
        if (".." in Path(relative).parts or "\\" in relative
                or not media.is_relative_to(root) or len(media.relative_to(root).parts) < 3):
            raise HTTPException(400, "输出模板必须包含作品目录、季目录和文件名，且不能超出下载目录")
        if replace_existing and old.get("info_hash"):
            qb = await qb_login(config.QBITTORRENT_URL, config.QBITTORRENT_USERNAME, config.QBITTORRENT_PASSWORD)
            if await get_torrents_by_hashes(qb, [torrent_hash], raise_on_error=True):
                raise HTTPException(409, "新种子已存在于 qBittorrent，未删除旧任务")
            await delete_torrent(qb, old["info_hash"], delete_files=True)
        task = asyncio.create_task(_generate_nfo(
            bangumi_id, sort, target, mapping,
            sub.get("series_name") or sub.get("name", str(bangumi_id)),
            replace_existing, metadata_ctx, template,
        ))
        _nfo_tasks.add(task)
        task.add_done_callback(_nfo_tasks.discard)
        result = await submit_torrent(torrent_path, target)
        mark_downloaded(
            bangumi_id, sort, guid, source, pub_date=pub_date,
            info_hash=result.info_hash, episode_mapping=mapping,
            download_path=result.target_path,
        )
        reset_fail_count(bangumi_id, sort)
        return result.info_hash
