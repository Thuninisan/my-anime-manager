"""Select RSS candidates using existing filters, numbering and history rules."""

import logging
from datetime import datetime, timezone

from ... import config
from ...data import (
    get_all_episodes, get_fail_count, get_episode_source, get_episode_pub_date,
    MAX_FAIL_COUNT,
)
from ...utils.rss_dates import publication_datetime, published_before_air_date
from .. import rss as rss_service

logger = logging.getLogger(__name__)

# Unknown sources retain the legacy priority of -1 at every call site.
SOURCE_PRIORITY = {"add": 0, "backup": 1, "primary": 2, "edit": 3}


async def fetch_passed_items(
    rss_url: str, filter_tags: list[str], bangumi_id: int,
    extra_exclude_patterns: list[str] | None = None,
    source: str = "primary",
    bgm_sortrange: list[int] | None = None,
    air_date: str = "",
    rss_offset: int | None = None,
    strict: bool = False,
    feed: dict | None = None,
) -> list[dict]:
    """Fetch RSS and return items that pass filter AND aren't downloaded yet.

    Boundary constraints:
    - Items are sorted by pub_date (earliest first) so older episodes
      are processed before newer ones.
    - Items with pub_date earlier than *air_date* (show premiere date)
      are skipped with a reason in the log.
    - Once all sorts in *bgm_sortrange* are covered (already downloaded
      + current candidates), remaining items are skipped.

    Uses Bangumi sort (not raw RSS episode number) for dedup, so the
    dedup key matches what ``mark_downloaded`` writes.

    *source* is the RSS feed type ("primary" or "backup").  It is used
    together with the existing download's source to enforce priority:
    add < backup < primary < edit — higher priority replaces lower.
    """
    try:
        if feed is None:
            feed = await rss_service.fetch_rss_snapshot(rss_url)
        feed = rss_service.apply_rss_state(
            feed, filter_tags, bangumi_id, extra_exclude_patterns)
    except Exception as e:
        logger.warning(f"   ⚠️ RSS 获取失败: {e}")
        if strict:
            raise
        return []

    # Sort RSS items by pub_date (earliest first)
    feed["items"].sort(key=lambda item: publication_datetime(item.get("pub_date", ""))
                       or datetime.max.replace(tzinfo=timezone.utc))

    # ── Track covered sorts (already downloaded) for sortrange limit ──
    downloaded_sorts: set[int] = set()
    for ep_sort_str in get_all_episodes(bangumi_id):
        try:
            downloaded_sorts.add(int(ep_sort_str))
        except (ValueError, TypeError):
            pass
    covered: set[int] = set(downloaded_sorts)
    seen_in_batch: set[int] = set()  # intra-batch dedup only

    # ── Log initial range state ──
    if bgm_sortrange and bgm_sortrange[0] > 0:
        needed = set(range(bgm_sortrange[0], bgm_sortrange[1] + 1))
        missing = needed - covered
        logger.debug("sortrange %s:已下载%d 缺失%d",
                     bgm_sortrange, len(covered & needed), len(missing))

    candidates = []
    skipped = 0
    examined = 0

    def log_skip(item: dict, reason: str) -> None:
        nonlocal skipped
        skipped += 1
        logger.info("RSS 资源排除: bangumi_id=%s source=%s episode=%s title=%r reason=%s",
                    bangumi_id, source, item.get("episode_number") or "?",
                    (item.get("title") or item.get("guid") or "")[:160], reason)

    for item in feed["items"]:
        examined += 1
        if item["excluded"]:
            patterns = list(dict.fromkeys([*config.RSS_EXCLUDE_PATTERNS,
                                           *(extra_exclude_patterns or [])]))
            matched = [pattern for pattern in patterns
                       if pattern in (item.get("guid") or item.get("title") or "")]
            log_skip(item, f"命中排除词: {matched or '(未找到匹配词)'}")
            continue
        if not item["passed"]:
            missing_tags = [tag for tag in filter_tags if tag not in item.get("tags", [])]
            log_skip(item, f"标签不匹配: 缺少 {missing_tags}; 资源标签 {item.get('tags', [])}")
            continue
        from ...domain.rss_episode import item_episode_ref
        from ..rss_episode_matcher import normalize_episode_number
        ref = item_episode_ref(item)
        rss_ep = ref["rss_episode_number"]
        if rss_ep is None:
            log_skip(item, "无法从标题识别集数")
            continue

        # ── Time filter: skip items published before show premiere ──
        item_pub_date = item.get("pub_date", "")
        if published_before_air_date(item_pub_date, air_date):
            log_skip(item, f"发布时间 {item_pub_date} 早于首播日期 {air_date}")
            continue

        # ── Assign sort: rss_ep + rss_offset ──
        # offset = first_bangumi_sort - smallest_rss_ep, computed during
        # enrichment.  This gives a direct linear mapping from RSS episode
        # numbers to Bangumi sort values.
        if rss_offset is None:
            log_skip(item, "RSS 集数偏移量未确定")
            continue  # can't determine sort without offset — skip
        sort = normalize_episode_number(ref, rss_offset)
        if bgm_sortrange and bgm_sortrange[0] > 0:
            if sort < bgm_sortrange[0] or sort > bgm_sortrange[1]:
                log_skip(item, f"映射集数 {sort} 不在 Bangumi 范围 {bgm_sortrange} 内 (offset={rss_offset})")
                continue  # outside expected range, skip
        item["sort"] = sort

        # ── Intra-batch duplicate filter ──
        # Only skip sorts already seen in *this* batch.  Previously
        # downloaded sorts are NOT skipped here — they go through the
        # source-priority check below so primary can replace backup.
        if sort in seen_in_batch:
            log_skip(item, f"本次轮询已有集数 {sort} 的候选资源")
            continue

        # Skip episodes that have already failed too many times
        fc = get_fail_count(bangumi_id, sort)
        if fc >= MAX_FAIL_COUNT:
            log_skip(item, f"集数 {sort} 已连续失败 {fc} 次，达到重试上限")
            continue

        existing_source = get_episode_source(bangumi_id, sort)

        if existing_source:
            feed_prio = SOURCE_PRIORITY.get(source, -1)
            exist_prio = SOURCE_PRIORITY.get(existing_source, -1)

            if feed_prio < exist_prio:
                log_skip(item, f"集数 {sort} 已由更高优先级来源 {existing_source} 下载")
                continue
            elif feed_prio == exist_prio:
                existing_pub = get_episode_pub_date(bangumi_id, sort)
                if item_pub_date and existing_pub and item_pub_date > existing_pub:
                    logger.info("EP%02d v2 detected [%s]: %s > %s",
                                rss_ep, source, item_pub_date, existing_pub)
                else:
                    log_skip(item, f"集数 {sort} 已由相同来源 {existing_source} 下载，发布时间未更新")
                    continue

        candidates.append(item)
        covered.add(sort)
        seen_in_batch.add(sort)

        # ── Stop when sortrange is fully covered ──
        if bgm_sortrange and bgm_sortrange[0] > 0:
            needed = set(range(bgm_sortrange[0], bgm_sortrange[1] + 1))
            if needed.issubset(covered):
                break

    if examined < len(feed["items"]):
        logger.info("RSS 剩余资源未检查: bangumi_id=%s source=%s count=%d reason=Bangumi 集数范围已覆盖",
                    bangumi_id, source, len(feed["items"]) - examined)
    logger.info("RSS 资源筛选完成: bangumi_id=%s source=%s total=%d candidates=%d excluded=%d",
                bangumi_id, source, len(feed["items"]), len(candidates), skipped)
    return candidates
