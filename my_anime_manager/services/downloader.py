"""RSS download worker — poll subscriptions, download new episodes via qBittorrent."""

import asyncio
import logging
import tempfile
import traceback
from pathlib import Path
import httpx

from .. import config
from ..clients.qbittorrent import login as qb_login, add_torrent, resume_torrent, delete_torrent
from ..clients.qbittorrent import (
    login as qb_login, add_torrent, resume_torrent,
)
from ..data import (
    get_tmdb_id, get_tvdb_id,
    list_subscriptions, mark_downloaded, get_episode_source,
    get_episode_pub_date, remove_episode_record,
    get_all_episodes,
    get_fail_count, increment_fail_count, reset_fail_count, MAX_FAIL_COUNT,
)
from . import rss as rss_service
from .enrich import (
    _bgm_ep_cache,
    _get_bangumi_episodes,
    enrich_subscription,
)
from .nfo import generate_metadata, format_download_path
from ..utils.torrent_hash import compute_info_hash

logger = logging.getLogger(__name__)

# Worker state
_worker_task: asyncio.Task | None = None
_worker_running = False
_worker_status: dict = {
    "running": False,
    "last_run": "",
    "downloaded": 0,
    "errors": [],
    "poll_interval_min": 30,
}
_worker_lock = asyncio.Lock()


def get_status() -> dict:
    return dict(_worker_status)


def get_config() -> dict:
    return {
        "poll_interval_min": _worker_status["poll_interval_min"],
        "running": _worker_running,
    }


async def start(poll_interval_min: int | None = None):
    global _worker_task, _worker_running
    if poll_interval_min is not None:
        _worker_status["poll_interval_min"] = poll_interval_min
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
    minutes = max(1, min(minutes, 1440))  # clamp 1–1440
    _worker_status["poll_interval_min"] = minutes
    if _worker_running:
        await stop()
        await start(minutes)
    return get_config()


async def run_once():
    """Manually trigger one full poll cycle."""
    await _poll_subscriptions()


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
    print(f"🔄 RSS 下载器启动 (间隔 {interval_sec // 60} 分钟)")
    while _worker_running:
        try:
            await _poll_subscriptions()
        except asyncio.CancelledError:
            break
        except Exception:
            traceback.print_exc()
        await asyncio.sleep(interval_sec)


async def _poll_subscriptions():
    import time
    async with _worker_lock:
        _worker_status["running"] = True
        _worker_status["errors"] = []
        try:
            subs = list_subscriptions()
            if not subs:
                print("📭 无 RSS 订阅")
                return

            print(f"📡 开始轮询 {len(subs)} 个订阅...")
            for sub in subs:
                try:
                    await _process_subscription(sub)
                except Exception as e:
                    msg = f"{sub.get('name', '?')}: {e}"
                    _worker_status["errors"].append(msg)
                    print(f"❌ {msg}")
            _worker_status["last_run"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            print(f"✅ 轮询完成 (下载 {_worker_status['downloaded']} 集)")
        finally:
            _worker_status["running"] = False


async def _process_subscription(sub: dict):
    bangumi_id = sub["bangumi_id"]

    # Skip completed subscriptions
    if sub.get("active") == 0:
        return

    primary = sub.get("primary", {})
    backup = sub.get("backup", {})
    bgm = sub.get("bgm", {})

    filter_tags = primary.get("filter_tags") or []
    name = sub.get("name", str(bangumi_id))

    bgm_sortrange = bgm.get("sortrange")
    air_date = bgm.get("air_date", "")

    # 1. Try primary RSS
    primary_exclude = primary.get("exclude_patterns") or []
    primary_rss = primary.get("rss_url", "")
    primary_offset = primary.get("offset")
    if primary_rss:
        primary_items = await _fetch_passed_items(
            primary_rss, filter_tags, bangumi_id,
            extra_exclude_patterns=primary_exclude, source="primary",
            bgm_sortrange=bgm_sortrange, air_date=air_date,
            rss_offset=primary_offset,
        )
        new_downloads = 0
        for item in primary_items:
            if await _download_item(item, bangumi_id, "primary", sub):
                new_downloads += 1
    else:
        new_downloads = 0

    # 2. Always check backup RSS — it may have episodes the primary doesn't
    backup_url = backup.get("rss_url", "")
    backup_offset = backup.get("offset")
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
        )
        for item in backup_items:
            if await _download_item(item, bangumi_id, "backup", sub):
                new_downloads += 1

    if new_downloads > 0:
        print(f"   📥 {name}: {new_downloads} 新集")
        _worker_status["downloaded"] += new_downloads


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
    """If all episodes in bgm_sortrange are downloaded, mark active=0."""
    bgm_sortrange = sub.get("bgm", {}).get("sortrange", [0, 0])
    if bgm_sortrange[0] <= 0:
        return
    episodes = get_all_episodes(bangumi_id)
    downloaded_sorts = {int(k) for k in episodes}
    expected = set(range(bgm_sortrange[0], bgm_sortrange[1] + 1))
    if expected and expected.issubset(downloaded_sorts):
        from ..data import update_subscription
        update_subscription(bangumi_id, {"active": 0})
        sub["active"] = 0
        print(f"   🏁 {sub.get('name', bangumi_id)}: 全部 {len(expected)} 集已下载，停止轮询")


async def _fetch_passed_items(
    rss_url: str, filter_tags: list[str], bangumi_id: int,
    extra_exclude_patterns: list[str] | None = None,
    source: str = "primary",
    bgm_sortrange: list[int] | None = None,
    air_date: str = "",
    rss_offset: int | None = None,
) -> list[dict]:
    """Fetch RSS and return items that pass filter AND aren't downloaded yet.

    Boundary constraints:
    - Items are sorted by pub_date (earliest first) so older episodes
      are processed before newer ones.
    - Items with pub_date earlier than *air_date* (show premiere date)
      are silently skipped.
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
        print(f"   ⚠️ RSS 获取失败: {e}")
        return []

    # Sort RSS items by pub_date (earliest first)
    feed["items"].sort(key=lambda item: item.get("pub_date") or "9999")

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
    for item in feed["items"]:
        if not item["passed"] or item["excluded"]:
            continue
        rss_ep = item.get("episode_number") or 0
        if not rss_ep:
            continue

        # ── Time filter: skip items published before show premiere ──
        item_pub_date = item.get("pub_date", "")
        if air_date and item_pub_date and item_pub_date < air_date:
            continue

        # ── Assign sort: rss_ep + rss_offset ──
        # offset = first_bangumi_sort - smallest_rss_ep, computed during
        # enrichment.  This gives a direct linear mapping from RSS episode
        # numbers to Bangumi sort values.
        if rss_offset is None:
            continue  # can't determine sort without offset — skip
        sort = rss_ep + rss_offset
        if bgm_sortrange and bgm_sortrange[0] > 0:
            if sort < bgm_sortrange[0] or sort > bgm_sortrange[1]:
                continue  # outside expected range, skip
        item["sort"] = sort

        # ── Intra-batch duplicate filter ──
        # Only skip sorts already seen in *this* batch.  Previously
        # downloaded sorts are NOT skipped here — they go through the
        # source-priority check below so primary can replace backup.
        if sort in seen_in_batch:
            continue

        # Skip episodes that have already failed too many times
        fc = get_fail_count(bangumi_id, sort)
        if fc >= MAX_FAIL_COUNT:
            if not hasattr(_fetch_passed_items, "_skip_logged"):
                _fetch_passed_items._skip_logged = set()  # type: ignore[attr-defined]
            skip_key = (bangumi_id, sort)
            if skip_key not in _fetch_passed_items._skip_logged:  # type: ignore[attr-defined]
                _fetch_passed_items._skip_logged.add(skip_key)  # type: ignore[attr-defined]
                print(f"      ⏭️ EP{rss_ep:02d} (sort={sort}) 已连续失败 {fc} 次，跳过")
            continue

        existing_source = get_episode_source(bangumi_id, sort)

        if existing_source:
            PRIORITY = {"add": 0, "backup": 1, "primary": 2, "edit": 3}
            feed_prio = PRIORITY.get(source, -1)
            exist_prio = PRIORITY.get(existing_source, -1)

            if feed_prio < exist_prio:
                continue
            elif feed_prio == exist_prio:
                existing_pub = get_episode_pub_date(bangumi_id, sort)
                if item_pub_date and existing_pub and item_pub_date > existing_pub:
                    logger.info("EP%02d v2 detected [%s]: %s > %s",
                                rss_ep, source, item_pub_date, existing_pub)
                else:
                    continue

        candidates.append(item)
        covered.add(sort)
        seen_in_batch.add(sort)

        # ── Stop when sortrange is fully covered ──
        if bgm_sortrange and bgm_sortrange[0] > 0:
            needed = set(range(bgm_sortrange[0], bgm_sortrange[1] + 1))
            if needed.issubset(covered):
                break

    return candidates


async def _download_item(item: dict, bangumi_id: int, source: str, sub: dict) -> bool:
    torrent_url = item["torrent_url"]
    guid = item["guid"]
    rss_ep_num = item.get("episode_number") or 0
    if not torrent_url or not rss_ep_num:
        return False

    print(f"      ⬇️ EP{rss_ep_num:02d} [{source}] {guid[:60]}...")

    bgm_subject_id = bangumi_id
    tmdb_id = get_tmdb_id(bangumi_id)
    tvdb_id = get_tvdb_id(bangumi_id)

    # ── Ensure subscription has enrichment fields ──────────────────
    bgm_season = sub.get("bgm", {}).get("season")
    if bgm_season is None:
        print(f"         🔗 订阅缺少 bgm_season，正在丰富化...")
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
            print(f"         ❌ 丰富化失败，将在下次轮询重试")
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
        print(f"         ⚠️ rss_ep={rss_ep_num} 无法映射到 sort，跳过")
        return False
    if sort != rss_ep_num:
        print(f"         📐 rss_ep={rss_ep_num} → sort={sort}")

    # ⛔ Guard: need at least one metadata source
    if not tmdb_id and not tvdb_id:
        print(f"         ⛔ TMDB/TVDB ID 均缺失，无法生成 NFO，跳过下载")
        print(f"         💡 请在订阅卡片中手动设置 TMDB 或 TVDB ID")
        increment_fail_count(bangumi_id, sort)
        return False

    bgm_sortrange = sub.get("bgm", {}).get("sortrange", [0, 0])
    if bgm_sortrange[0] > 0 and (sort < bgm_sortrange[0] or sort > bgm_sortrange[1]):
        print(f"         ⚠️ sort={sort} 超出 bgm_sortrange={bgm_sortrange}，但仍继续处理")

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
                    print(f"         🗑️ 删除旧种子 [{old_hash[:12]}…]，替换为 {source}")
                except Exception as e:
                    print(f"         ⚠️ 删除旧种子失败: {e}")

    # ── Download .torrent ──────────────────────────────────────────
    try:
        torrent_content = await _download_torrent_file(torrent_url)
    except httpx.HTTPStatusError as e:
        status = e.response.status_code if hasattr(e.response, 'status_code') else '?'
        print(f"      ❌ 下载 .torrent 失败 (HTTP {status}): {torrent_url[:80]}...")
        # Track failure count so we can eventually give up on dead URLs
        fail_count = increment_fail_count(bangumi_id, sort)
        if fail_count >= MAX_FAIL_COUNT:
            print(f"      🚫 已连续失败 {fail_count} 次，跳过此集（将不再重试）")
        return False
    except Exception as e:
        exc_name = type(e).__name__
        print(f"      ❌ 下载 .torrent 失败 [{exc_name}]: {e}")
        print(f"         URL: {torrent_url[:100]}...")
        # Track failure count so we can eventually give up on dead URLs
        fail_count = increment_fail_count(bangumi_id, sort)
        if fail_count >= MAX_FAIL_COUNT:
            print(f"      🚫 已连续失败 {fail_count} 次，跳过此集（将不再重试）")
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
        print(f"      ✅ 种子已添加 [{info_hash[:12]}…]")
    except Exception as e:
        print(f"      ❌ qBittorrent 添加失败: {e}")
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
                print(f"      ❌ NFO 生成失败：TVDB 未找到对应剧集，种子已删除")
                await delete_torrent(qb, info_hash, delete_files=False)
                return False
        except Exception as e:
            print(f"      ❌ NFO 生成失败: {e}，种子已删除")
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
