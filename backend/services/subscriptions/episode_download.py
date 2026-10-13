"""Turn an RSS candidate into a submitted episode, retaining RSS retry policy.

Owns enrichment fallback, automatic source replacement and temporary torrent
cleanup. Manual uploads enter episode_submission directly with their own policy.
"""

import logging
import tempfile
from pathlib import Path

import httpx

from ... import config
from ...clients.qbittorrent import login as qb_login, delete_torrent
from ...data import (
    list_subscriptions, get_all_episodes, get_episode_source, get_episode_pub_date,
    remove_episode_record, increment_fail_count, MAX_FAIL_COUNT,
)
from ...logging.logging_config import safe_url
from ..enrich import enrich_subscription
from ..nfo.metadata_context import MetadataContext
from ..downloads.episode_submission import submit_episode_torrent
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

    # ── Replacement: delete old torrent from qBittorrent ─────────
    item_pub_date = item.get("pub_date", "")
    existing_source = get_episode_source(bangumi_id, sort)
    if existing_source:
        new_prio = SOURCE_PRIORITY.get(source, -1)
        exist_prio = SOURCE_PRIORITY.get(existing_source, -1)

        should_replace = False
        if new_prio > exist_prio:
            # Higher-priority source always replaces lower (e.g. primary → backup)
            should_replace = True
        elif new_prio == exist_prio:
            # Same-source v2: only replace if pub_date is newer
            existing_pub = get_episode_pub_date(bangumi_id, sort)
            if item_pub_date and existing_pub and item_pub_date > existing_pub:
                should_replace = True

        if should_replace:
            # Fetch old info_hash to delete the old torrent
            old_entries = get_all_episodes(bangumi_id)
            old_entry = old_entries.get(str(sort))
            if old_entry and old_entry.get("info_hash"):
                old_hash = old_entry["info_hash"]
                try:
                    qb = await qb_login(config.QBITTORRENT_URL, config.QBITTORRENT_USERNAME, config.QBITTORRENT_PASSWORD)
                    await delete_torrent(qb, old_hash, delete_files=True)
                    remove_episode_record(bangumi_id, sort)
                    logger.debug(f"         🗑️ 删除旧种子 [{old_hash[:12]}…]，替换为 {source}")
                except Exception as e:
                    logger.warning(f"         ⚠️ 删除旧种子失败: {e}")

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
        await submit_episode_torrent(
            tmp_path, bangumi_id, sort, source, sub=sub, guid=guid,
            pub_date=item.get("pub_date", ""),
            metadata_ctx=metadata_ctx, rss_ref=ref,
        )
    except Exception:
        logger.exception("RSS 单集提交失败: bangumi_id=%s sort=%s", bangumi_id, sort)
        return False
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    return True
