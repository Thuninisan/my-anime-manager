"""Poll one subscription: primary/backup feeds, offsets and completion.

Returns the successful submission count without owning scheduler state. Callers
must use worker entry points to serialize full and initial subscription polls.
A single poll reuses its feed snapshots and MetadataContext across both sources.
"""

import logging

from ...data import list_subscriptions, get_all_episodes
from ...logging.logging_config import safe_url
from . import feeds as rss_service
from ..metadata.providers.bangumi import invalidate_episode_cache, get_cached_episodes
from .inference import _compute_rss_offset
from ..metadata.context import MetadataContext
from . import selection, episode_download

logger = logging.getLogger(__name__)


async def process_subscription(
    sub: dict, *, strict: bool = False,
    primary_feed: dict | None = None, backup_feed: dict | None = None,
) -> int:
    """Process both feeds and return successful submissions; caller owns the lock."""
    bangumi_id = sub["bangumi_id"]
    from ...db.mapping_changes import blocked
    if blocked(bangumi_id):
        return 0
    metadata_ctx = MetadataContext()

    # A full poll may have loaded its subscription list while enrichment was
    # still running. Read the latest record before using its nested offsets.
    sub = next((item for item in list_subscriptions()
                if item["bangumi_id"] == bangumi_id), sub)

    # Skip completed subscriptions
    if sub.get("active") == 0:
        return 0

    primary = sub.get("primary", {})
    backup = sub.get("backup", {})
    bgm = sub.get("bgm", {})

    filter_tags = primary.get("filter_tags") or []
    name = sub.get("name", str(bangumi_id))

    bgm_sortrange = bgm.get("sortrange")
    air_date = bgm.get("air_date", "")

    snapshots: dict[str, dict] = {}
    fetch_errors: dict[str, Exception] = {}
    for url, supplied in ((primary.get("rss_url", ""), primary_feed),
                          (backup.get("rss_url", ""), backup_feed)):
        if url and supplied is not None:
            snapshots[url] = supplied

    async def get_snapshot(url: str) -> dict:
        if url in fetch_errors:
            raise fetch_errors[url]
        if url not in snapshots:
            try:
                snapshots[url] = await rss_service.fetch_rss_snapshot(url)
            except Exception as exc:
                fetch_errors[url] = exc
                raise
        return snapshots[url]

    async def resolve_offset(feed: dict, key: str) -> int | None:
        offset = feed.get("offset")
        if offset is not None:
            return offset
        rss_url = feed.get("rss_url", "")
        if not rss_url or not bgm_sortrange or bgm_sortrange[0] <= 0 or not air_date:
            return None
        try:
            snapshot = await get_snapshot(rss_url)
        except Exception:
            logger.warning("RSS 获取失败: %s", safe_url(rss_url), exc_info=True)
            return None
        smallest = _compute_rss_offset(snapshot, air_date)
        if smallest is None:
            logger.warning("RSS 集数偏移量仍未确定: bangumi_id=%s source=%s", bangumi_id, key)
            return None
        offset = bgm_sortrange[0] - smallest
        from ...data import set_subscription_rss_offset
        if set_subscription_rss_offset(bangumi_id, key, offset):
            feed["offset"] = offset
            logger.info("RSS 集数偏移量已补算: bangumi_id=%s source=%s offset=%d",
                        bangumi_id, key, offset)
        return offset

    # 1. Try primary RSS
    primary_exclude = primary.get("exclude_patterns") or []
    primary_rss = primary.get("rss_url", "")
    primary_offset = await resolve_offset(primary, "primary")
    if primary_rss:
        try:
            primary_snapshot = await get_snapshot(primary_rss)
        except Exception:
            if strict:
                raise
            logger.warning("RSS 获取失败: %s", safe_url(primary_rss))
            primary_snapshot = {"items": []}
        primary_items = await selection.fetch_passed_items(
            primary_rss, filter_tags, bangumi_id,
            extra_exclude_patterns=primary_exclude, source="primary",
            bgm_sortrange=bgm_sortrange, air_date=air_date,
            rss_offset=primary_offset,
            strict=strict,
            feed=primary_snapshot,
        )
        new_downloads = 0
        for item in primary_items:
            if await episode_download.download_item(item, bangumi_id, "primary", sub, metadata_ctx=metadata_ctx):
                new_downloads += 1
            elif strict:
                raise RuntimeError(f"主订阅资源处理失败: {item.get('title', '?')}")
    else:
        new_downloads = 0

    # 2. Always check backup RSS — it may have episodes the primary doesn't
    backup_url = backup.get("rss_url", "")
    backup_offset = await resolve_offset(backup, "backup")
    if backup_url:
        try:
            backup_snapshot = await get_snapshot(backup_url)
        except Exception:
            if strict:
                raise
            logger.warning("RSS 获取失败: %s", safe_url(backup_url))
            backup_snapshot = {"items": []}
        backup_tags = backup.get("filter_tags")
        if backup_tags is None:
            backup_tags = filter_tags
        backup_exclude = backup.get("exclude_patterns") or []
        backup_items = await selection.fetch_passed_items(
            backup_url, backup_tags, bangumi_id,
            extra_exclude_patterns=backup_exclude, source="backup",
            bgm_sortrange=bgm_sortrange, air_date=air_date,
            rss_offset=backup_offset,
            strict=strict,
            feed=backup_snapshot,
        )
        for item in backup_items:
            if await episode_download.download_item(item, bangumi_id, "backup", sub, metadata_ctx=metadata_ctx):
                new_downloads += 1
            elif strict:
                raise RuntimeError(f"副订阅资源处理失败: {item.get('title', '?')}")

    if new_downloads > 0:
        await _refresh_sortrange(bangumi_id, sub)
        await _check_completion(bangumi_id, sub)
        logger.info("RSS 下载新集: bangumi_id=%s name=%s count=%d", bangumi_id, name, new_downloads)
    return new_downloads


async def _refresh_sortrange(bangumi_id: int, sub: dict):
    """Re-fetch Bangumi episode list and update bgm.sortrange in the subscription.

    Bangumi entries for currently-airing shows often have incomplete episode
    lists (fewer sorts than the final count).  After each successful download
    we refresh the sort range so ``_check_completion`` always sees the latest
    data and won't prematurely mark a subscription as completed.
    """
    sub_bak = sub.get("bgm", {}).get("sortrange")
    try:
        # Clear cached episodes so we get the latest from the API
        invalidate_episode_cache(bangumi_id)
        eps = await get_cached_episodes(bangumi_id)
        sorts = [e["sort"] for e in eps if e.get("sort") is not None]
        new_range = [min(sorts), max(sorts)] if sorts else [0, 0]

        if sub_bak != new_range:
            sub.setdefault("bgm", {})["sortrange"] = new_range
            from ...data import update_subscription
            bgm_data = dict(sub.get("bgm", {}))
            bgm_data["sortrange"] = new_range
            update_subscription(bangumi_id, {"bgm": bgm_data})
            logger.info("bgm_sortrange refreshed: %s → %s", sub_bak, new_range)
    except Exception:
        logger.warning("Failed to refresh bgm_sortrange (non-fatal)", exc_info=True)
        # Revert cache pop on error so next call can retry with existing cache
        invalidate_episode_cache(bangumi_id)


async def _check_completion(bangumi_id: int, sub: dict):
    """Use primary RSS for completion, or backup RSS if it is the only feed."""
    bgm_sortrange = sub.get("bgm", {}).get("sortrange", [0, 0])
    if bgm_sortrange[0] <= 0:
        return
    if sub.get("primary", {}).get("rss_url"):
        required_source = "primary"
    elif sub.get("backup", {}).get("rss_url"):
        required_source = "backup"
    else:
        return
    episodes = get_all_episodes(bangumi_id)
    source_sorts = {
        int(sort) for sort, episode in episodes.items()
        if episode.get("source") == required_source
    }
    expected = set(range(bgm_sortrange[0], bgm_sortrange[1] + 1))
    if expected and expected.issubset(source_sorts):
        from ...data import update_subscription
        update_subscription(bangumi_id, {"active": 0})
        sub["active"] = 0
        feed_name = "主订阅" if required_source == "primary" else "副订阅"
        logger.info("RSS 订阅已完成: bangumi_id=%s source=%s episodes=%d",
                    bangumi_id, feed_name, len(expected))
