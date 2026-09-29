"""RSS download worker — poll subscriptions, download new episodes via qBittorrent."""

import asyncio
import logging
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
import httpx

from fastapi import HTTPException

from .. import config
from ..clients.qbittorrent import login as qb_login, add_torrent, resume_torrent, delete_torrent, get_torrent_files
from ..data import (
    get_tmdb_id, get_tvdb_id,
    list_subscriptions, mark_downloaded, get_episode_source,
    get_episode_pub_date, remove_episode_record,
    get_all_episodes,
    get_fail_count, increment_fail_count, reset_fail_count, MAX_FAIL_COUNT,
)
from . import rss as rss_service
from ..utils.rss_dates import publication_datetime, published_before_air_date
from .enrich import (
    _bgm_ep_cache,
    _get_bangumi_episodes,
    _compute_rss_offset,
    enrich_subscription,
)
from .nfo import generate_metadata, format_download_path
from ..utils.torrent_hash import compute_info_hash
from ..logging.logging_config import new_operation_id, operation_context, safe_url

logger = logging.getLogger(__name__)

# Worker state
_worker_task: asyncio.Task | None = None
_worker_running = False
_worker_status: dict = {
    "running": False,
    "last_run": "",
    "downloaded": 0,
    "errors": [],
    "poll_interval_min": config.RSS_POLL_INTERVAL_MIN,
}
_worker_lock = asyncio.Lock()


def get_status() -> dict:
    return {**_worker_status, "scheduler_running": _worker_running}


def get_config() -> dict:
    _worker_status["poll_interval_min"] = config.RSS_POLL_INTERVAL_MIN
    return {
        "poll_interval_min": _worker_status["poll_interval_min"],
        "running": _worker_running,
    }


async def start(poll_interval_min: int | None = None):
    global _worker_task, _worker_running
    _worker_status["poll_interval_min"] = config.RSS_POLL_INTERVAL_MIN
    if _worker_running:
        return
    _worker_running = True
    interval = _worker_status["poll_interval_min"] * 60
    _worker_task = asyncio.create_task(_run_loop(interval))


async def stop():
    global _worker_task, _worker_running
    _worker_running = False
    if _worker_task:
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
        _worker_task = None


async def set_interval(minutes: int) -> dict:
    """Change polling interval.  Restarts the worker if it's running."""
    config.update({"RSS_POLL_INTERVAL_MIN": minutes})
    await apply_interval()
    return get_config()


async def apply_interval() -> None:
    minutes = config.RSS_POLL_INTERVAL_MIN
    _worker_status["poll_interval_min"] = minutes
    if _worker_running:
        await stop()
        await start(minutes)


async def run_once():
    """Manually trigger one full poll cycle."""
    await _poll_subscriptions()


async def poll_subscription(bangumi_id: int) -> dict:
    """Poll one saved subscription without changing the full worker status."""
    async with _worker_lock:
        with operation_context(new_operation_id("rss")):
            sub = next((item for item in list_subscriptions()
                        if item["bangumi_id"] == bangumi_id), None)
            if sub is None:
                raise ValueError(f"订阅不存在: {bangumi_id}")
            if sub.get("active") == 0:
                return {"state": "skipped", "downloaded": 0, "message": "订阅已停用"}
            downloaded = await _process_subscription(sub, strict=True)
            return {"state": "completed", "downloaded": downloaded, "message": "首次轮询完成"}


async def check_qbit() -> dict:
    """Test qBittorrent connectivity and return status info."""
    try:
        qb = await qb_login(config.QBITTORRENT_URL, config.QBITTORRENT_USERNAME, config.QBITTORRENT_PASSWORD)
        info = qb.app.version or "?"
        return {"ok": True, "url": config.QBITTORRENT_URL, "version": info, "error": ""}
    except Exception as e:
        return {"ok": False, "url": config.QBITTORRENT_URL, "version": "", "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════
# .torrent download helper — delegates retry to shared fetch_with_retry
# ═══════════════════════════════════════════════════════════════════════

async def _download_torrent_file(torrent_url: str, max_retries: int = 3) -> bytes:
    """Download a .torrent file.  Retry is handled by fetch_with_retry.

    The only extra logic beyond fetch_with_retry is detecting HTML error
    pages that are served with 200 OK (some CDNs do this).
    """
    from ..utils.http_retry import fetch_with_retry as _fetch

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


# ═══════════════════════════════════════════════════════════════════════
# Internal — polling loop
# ═══════════════════════════════════════════════════════════════════════

async def _run_loop(interval_sec: int):
    logger.info("RSS 下载器启动: interval_min=%d", interval_sec // 60)
    while _worker_running:
        try:
            await _poll_subscriptions()
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("RSS 下载器轮询异常")
        await asyncio.sleep(interval_sec)


async def _poll_subscriptions():
    async with _worker_lock:
        with operation_context(new_operation_id("rss")):
            await _poll_subscriptions_with_context()


async def _poll_subscriptions_with_context():
    _worker_status["running"] = True
    _worker_status["errors"] = []
    try:
        subs = list_subscriptions()
        if not subs:
            logger.info("RSS 轮询结束: 无订阅")
            return

        logger.info("RSS 轮询开始: subscriptions=%d", len(subs))
        for sub in subs:
            try:
                await _process_subscription(sub)
            except Exception as e:
                msg = f"{sub.get('name', '?')}: {e}"
                _worker_status["errors"].append(msg)
                logger.exception("RSS 订阅处理失败: bangumi_id=%s", sub.get("bangumi_id"))
        _worker_status["last_run"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        logger.info("RSS 轮询完成: downloaded=%d errors=%d",
                    _worker_status["downloaded"], len(_worker_status["errors"]))
    finally:
        _worker_status["running"] = False


async def _process_subscription(sub: dict, *, strict: bool = False) -> int:
    bangumi_id = sub["bangumi_id"]

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

    async def resolve_offset(feed: dict, key: str) -> int | None:
        offset = feed.get("offset")
        if offset is not None:
            return offset
        rss_url = feed.get("rss_url", "")
        if not rss_url or not bgm_sortrange or bgm_sortrange[0] <= 0 or not air_date:
            return None
        smallest = await _compute_rss_offset(rss_url, air_date)
        if smallest is None:
            logger.warning("RSS 集数偏移量仍未确定: bangumi_id=%s source=%s", bangumi_id, key)
            return None
        offset = bgm_sortrange[0] - smallest
        from ..data import set_subscription_rss_offset
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
        primary_items = await _fetch_passed_items(
            primary_rss, filter_tags, bangumi_id,
            extra_exclude_patterns=primary_exclude, source="primary",
            bgm_sortrange=bgm_sortrange, air_date=air_date,
            rss_offset=primary_offset,
            strict=strict,
        )
        new_downloads = 0
        for item in primary_items:
            if await _download_item(item, bangumi_id, "primary", sub):
                new_downloads += 1
            elif strict:
                raise RuntimeError(f"主订阅资源处理失败: {item.get('title', '?')}")
    else:
        new_downloads = 0

    # 2. Always check backup RSS — it may have episodes the primary doesn't
    backup_url = backup.get("rss_url", "")
    backup_offset = await resolve_offset(backup, "backup")
    if backup_url:
        backup_tags = backup.get("filter_tags")
        if backup_tags is None:
            backup_tags = filter_tags
        backup_exclude = backup.get("exclude_patterns") or []
        backup_items = await _fetch_passed_items(
            backup_url, backup_tags, bangumi_id,
            extra_exclude_patterns=backup_exclude, source="backup",
            bgm_sortrange=bgm_sortrange, air_date=air_date,
            rss_offset=backup_offset,
            strict=strict,
        )
        for item in backup_items:
            if await _download_item(item, bangumi_id, "backup", sub):
                new_downloads += 1
            elif strict:
                raise RuntimeError(f"副订阅资源处理失败: {item.get('title', '?')}")

    if new_downloads > 0:
        logger.info("RSS 下载新集: bangumi_id=%s name=%s count=%d", bangumi_id, name, new_downloads)
        if not strict:
            _worker_status["downloaded"] += new_downloads
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
        _bgm_ep_cache.pop(bangumi_id, None)
        eps = await _get_bangumi_episodes(bangumi_id)
        sorts = [e.get("sort") or e.get("ep", 0) for e in eps]
        new_range = [min(sorts), max(sorts)] if sorts else [0, 0]

        if sub_bak != new_range:
            sub.setdefault("bgm", {})["sortrange"] = new_range
            from ..data import update_subscription
            bgm_data = dict(sub.get("bgm", {}))
            bgm_data["sortrange"] = new_range
            update_subscription(bangumi_id, {"bgm": bgm_data})
            logger.info("bgm_sortrange refreshed: %s → %s", sub_bak, new_range)
    except Exception:
        logger.warning("Failed to refresh bgm_sortrange (non-fatal)", exc_info=True)
        # Revert cache pop on error so next call can retry with existing cache
        _bgm_ep_cache.pop(bangumi_id, None)


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
        from ..data import update_subscription
        update_subscription(bangumi_id, {"active": 0})
        sub["active"] = 0
        feed_name = "主订阅" if required_source == "primary" else "副订阅"
        logger.info("RSS 订阅已完成: bangumi_id=%s source=%s episodes=%d",
                    bangumi_id, feed_name, len(expected))


async def _fetch_passed_items(
    rss_url: str, filter_tags: list[str], bangumi_id: int,
    extra_exclude_patterns: list[str] | None = None,
    source: str = "primary",
    bgm_sortrange: list[int] | None = None,
    air_date: str = "",
    rss_offset: int | None = None,
    strict: bool = False,
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
        feed = await rss_service.fetch_and_parse_rss(
            rss_url, filter_tags, bangumi_id,
            extra_exclude_patterns=extra_exclude_patterns,
        )
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
        rss_ep = item.get("episode_number") or 0
        if not rss_ep:
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
        sort = rss_ep + rss_offset
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
            PRIORITY = {"add": 0, "backup": 1, "primary": 2, "edit": 3}
            feed_prio = PRIORITY.get(source, -1)
            exist_prio = PRIORITY.get(existing_source, -1)

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


async def _download_item(item: dict, bangumi_id: int, source: str, sub: dict) -> bool:
    torrent_url = item["torrent_url"]
    guid = item["guid"]
    rss_ep_num = item.get("episode_number") or 0
    if not torrent_url or not rss_ep_num:
        return False

    logger.debug(f"      ⬇️ EP{rss_ep_num:02d} [{source}] {guid[:60]}...")

    bgm_subject_id = bangumi_id
    tmdb_id = get_tmdb_id(bangumi_id)
    tvdb_id = get_tvdb_id(bangumi_id)

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
        )
        if enriched:
            # Pop offsets before top-level update; write to nested keys
            primary_offset = enriched.pop("primary_offset", None)
            backup_offset = enriched.pop("backup_offset", None)
            sub.update(enriched)
            from ..data import update_subscription, set_subscription_rss_offset
            update_subscription(bangumi_id, enriched)
            if primary_offset is not None:
                set_subscription_rss_offset(bangumi_id, "primary", primary_offset)
                sub.setdefault("primary", {})["offset"] = primary_offset
            if backup_offset is not None:
                set_subscription_rss_offset(bangumi_id, "backup", backup_offset)
                sub.setdefault("backup", {})["offset"] = backup_offset
        else:
            logger.error(f"         ❌ 丰富化失败，将在下次轮询重试")
            return False

    bgm_season = sub.get("bgm", {}).get("season", 1)
    tmdb_season = sub.get("tmdb", {}).get("season")
    # Re-read tmdb_id/tvdb_id: enrichment may have just persisted them
    if not tmdb_id:
        tmdb_id = get_tmdb_id(bangumi_id) or sub.get("tmdb", {}).get("id") or 0
    if not tvdb_id:
        tvdb_id = get_tvdb_id(bangumi_id) or sub.get("tvdb", {}).get("id") or 0

    # ── Match RSS episode to Bangumi sort ──────────────────────────
    # sort is assigned by _fetch_passed_items via rss_ep + rss_offset.
    sort = item.get("sort") or 0
    if not sort:
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
        PRIORITY = {"add": 0, "backup": 1, "primary": 2, "edit": 3}
        new_prio = PRIORITY.get(source, -1)
        exist_prio = PRIORITY.get(existing_source, -1)

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

    # ── Compute info-hash from the .torrent file ───────────────────
    torrent_hash = compute_info_hash(tmp_path)

    # ── Compute download paths from template ───────────────────────
    show_name = sub.get("name", str(bangumi_id))
    bgm = sub.get("bgm", {})
    bgm_subject_name = bgm.get("subject_name") or show_name
    series_name = sub.get("series_name") or show_name
    tvdb_ep_val = sort + sub.get("tvdb", {}).get("ep_offset", 0)
    rss_base = config.RSS_DOWNLOAD_PATH or config.QBITTORRENT_SAVE_PATH
    template = config.RSS_PATH_TEMPLATE
    rel_path = format_download_path(template, sub, sort=sort, tvdb_episode=tvdb_ep_val).lstrip("/")
    rel_dir = str(Path(rel_path).parent)
    season_dir = str(Path(rss_base) / rel_dir)
    show_dir = str(Path(season_dir).parent)

    # ── Add to qBittorrent ─────────────────────────────────────────
    # Pass the raw string (POSIX path) — don't let Path() convert to Windows style
    try:
        qb = await qb_login(config.QBITTORRENT_URL, config.QBITTORRENT_USERNAME, config.QBITTORRENT_PASSWORD)
        info_hash = await add_torrent(qb, tmp_path, rss_base, guid)
        logger.info("RSS 种子已添加: bangumi_id=%s episode=%s hash=%s",
                    bangumi_id, rss_ep_num, info_hash[:12])
    except Exception as e:
        logger.error(f"      ❌ qBittorrent 添加失败: {e}")
        Path(tmp_path).unlink(missing_ok=True)
        return False
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    # ── Generate metadata + rename ─────────────────────────────────
    if tmdb_id or tvdb_id:
        try:
            from ..clients.qbittorrent import get_torrent_files
            files = await get_torrent_files(qb, info_hash)
            old_path = files[0]["name"] if files else guid
            tvdb_meta = sub.get("tvdb", {})
            tmdb_meta = sub.get("tmdb", {})
            success = await generate_metadata(
                qb, info_hash, bangumi_id, sort,
                bgm_subject_id, tmdb_id, show_name,
                old_path, guid,
                bgm_season=bgm_season,
                tmdb_season=tmdb_season,
                tmdb_ep_offset=tmdb_meta.get("ep_offset", 0),
                tvdb_id=tvdb_id or tvdb_meta.get("id") or 0,
                tvdb_season=tvdb_meta.get("season"),
                tvdb_ep_offset=tvdb_meta.get("ep_offset", 0),
                tvdb_ep=tvdb_ep_val,
                season_dir=season_dir,
                show_dir=show_dir,
                bgm_subject_name=bgm_subject_name,
                series_name=series_name,
            )
            if not success:
                logger.error(f"      ❌ NFO 生成失败：TVDB 未找到对应剧集，种子已删除")
                await delete_torrent(qb, info_hash, delete_files=False)
                return False
        except Exception as e:
            logger.error(f"      ❌ NFO 生成失败: {e}，种子已删除")
            await delete_torrent(qb, info_hash, delete_files=False)
            return False

    # ── Resume download ────────────────────────────────────────────
    try:
        await resume_torrent(qb, info_hash)
    except Exception:
        pass  # resume might fail if auto-started

    tmdb_ep_calc = sort + sub.get("tmdb", {}).get("ep_offset", 0)
    mark_downloaded(bangumi_id, sort, item.get("rss_url", ""), guid, source,
                    pub_date=item.get("pub_date", ""), info_hash=torrent_hash,
                    tvdb_ep=tvdb_ep_val, tmdb_ep_calc=tmdb_ep_calc)

    # Clear any previous failure count after a successful download
    reset_fail_count(bangumi_id, sort)

    # Refresh sortrange from Bangumi (newly-airing shows often grow their
    # episode list over time, so the initial range may be too small)
    await _refresh_sortrange(bangumi_id, sub)

    # Check if all episodes in the sort range are now downloaded
    await _check_completion(bangumi_id, sub)

    return True


def resolve_episode_paths(bangumi_id: int, sort: int) -> tuple[dict, str, str]:
    """Resolve the subscription and download dirs for one episode.

    Shared by the download-history routes (upload/replace) and NFO
    regeneration.  Returns ``(sub, season_dir, show_dir)``.  Raises
    HTTPException(404) when the subscription does not exist.
    """
    subs = list_subscriptions()
    sub = next((s for s in subs if s["bangumi_id"] == bangumi_id), None)
    if not sub:
        raise HTTPException(404, "订阅不存在")

    tvdb_ep_val = sort + sub.get("tvdb", {}).get("ep_offset", 0)
    rss_base = config.RSS_DOWNLOAD_PATH or config.QBITTORRENT_SAVE_PATH
    rel_path = format_download_path(
        config.RSS_PATH_TEMPLATE, sub, sort=sort, tvdb_episode=tvdb_ep_val,
    ).lstrip("/")
    rel_dir = str(Path(rel_path).parent)
    season_dir = str(Path(rss_base) / rel_dir)
    show_dir = str(Path(season_dir).parent)
    return sub, season_dir, show_dir


async def regen_episode_nfo(bangumi_id: int, sort: int) -> None:
    """Regenerate NFO for one downloaded episode using its stored overrides.

    Everything is derived server-side (subscription, per-episode TMDB
    overrides, paths) — callers only need to identify the episode.
    NFO-only: rewrites NFO + images on disk and never touches qBittorrent
    (no rename), so it works even after the torrent has been removed.
    Raises HTTPException(4xx) on missing data, HTTPException(500) on failure.
    """
    sub, season_dir, show_dir = resolve_episode_paths(bangumi_id, sort)
    if not sub.get("tmdb", {}).get("id"):
        raise HTTPException(400, "订阅未关联 TMDB ID，无法生成 NFO")
    ep = get_all_episodes(bangumi_id).get(str(sort))
    if not ep:
        raise HTTPException(404, "该集的下载记录不存在")

    show_name = sub.get("name", str(bangumi_id))
    series_name = sub.get("series_name") or show_name
    bgm_season = sub.get("bgm", {}).get("season", 1)
    # Same episode-number inputs as the download flow (downloader.py) so the
    # NFO filename matches the one computed when the episode was downloaded.
    tvdb_meta = sub.get("tvdb", {})
    tmdb_meta = sub.get("tmdb", {})
    tvdb_ep_val = sort + tvdb_meta.get("ep_offset", 0)

    # qb_client / info_hash / old_torrent_path are only consumed by the
    # rename step, which regen skips (rename_in_qbit=False) — placeholders.
    ok = await generate_metadata(
        None, "", bangumi_id, sort,
        bangumi_id, sub["tmdb"]["id"], show_name,
        "", "",
        bgm_season=bgm_season,
        tmdb_season=tmdb_meta.get("season"),
        tmdb_ep_offset=tmdb_meta.get("ep_offset", 0),
        tvdb_id=tvdb_meta.get("id") or 0,
        tvdb_season=tvdb_meta.get("season"),
        tvdb_ep_offset=tvdb_meta.get("ep_offset", 0),
        tvdb_ep=tvdb_ep_val,
        season_dir=season_dir, show_dir=show_dir,
        series_name=series_name,
        rename_in_qbit=False,
        overwrite=True,
    )
    if not ok:
        raise HTTPException(500, "NFO 生成失败")
    logger.info("regen-nfo: NFO regenerated for bangumi=%d sort=%d", bangumi_id, sort)
