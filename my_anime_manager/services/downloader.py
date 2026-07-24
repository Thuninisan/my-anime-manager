"""RSS download worker — poll subscriptions, download new episodes via qBittorrent."""

import asyncio
import logging
import tempfile
import traceback
from pathlib import Path
from typing import Any, Callable

import httpx

from .. import config
from ..clients.qbittorrent import login as qb_login, add_torrent, resume_torrent, delete_torrent
from ..clients.qbittorrent import (
    login as qb_login, add_torrent, rename_file, resume_torrent,
)
from ..clients import bangumi as bgm_client
from ..clients.bangumi import get_episodes as bgm_get_episodes, get_subject
from ..data import (
    get_tmdb_id, get_tmdb_season, get_tvdb_id, get_tvdb_season, get_bangumi_name, set_tmdb_id as data_set_tmdb_id,
    list_subscriptions, mark_downloaded, get_episode_source,
    get_episode_pub_date, remove_episode_record,
    get_all_episodes,
    get_fail_count, increment_fail_count, reset_fail_count, MAX_FAIL_COUNT,
)
from . import rss as rss_service, tmdb as tmdb_service
from .enrich import (
    _bgm_ep_cache,
    _get_bangumi_episodes,
    _match_rss_ep_to_sort,
    _get_bangumi_ep_id,
    _get_bangumi_relations,
    enrich_subscription,
)
from .nfo_generator import generate_episode_nfo, generate_tv_show_nfo, generate_season_nfo
from .image_downloader import download_episode_thumb, download_show_images, download_season_poster
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
# NFO & file structure generation
# ═══════════════════════════════════════════════════════════════════════

def format_download_path(
    template: str, sub: dict, sort: int = 0, ext: str = "",
    bangumi_sort: int = 0, bangumi_ep: int = 0,
    tvdb_episode: int = 0, tmdb_episode: int = 0,
) -> str:
    """Format a download path template with subscription and episode variables.

    Uses Python ``str.format()`` syntax.  Available variables:

    ==================== ============================================
    ``{series_name}``    Root series name (from Bangumi chain)
    ``{bangumi_title}``  Current season Bangumi entry name
    ``{bgm_season}``     Bangumi chain position (int)
    ``{tvdb_season}``    TVDB season number (int)
    ``{tmdb_season}``    TMDB season number (int)
    ``{bangumi_sort}``   Bangumi sort number (sequential within entry)
    ``{bangumi_ep}``     Bangumi canonical episode number
    ``{tvdb_episode}``   Matched TVDB episode number
    ``{tmdb_episode}``   Matched TMDB episode number
    ``{sort}``           Alias for ``{bangumi_sort}`` (deprecated)
    ==================== ============================================

    Format specs are supported, e.g. ``{tvdb_episode:02d}`` → ``05``.
    The *ext* parameter (e.g. ``".mkv"``) is appended after formatting.
    """
    bgm = sub.get("bgm", {})
    tvdb = sub.get("tvdb", {})
    tmdb = sub.get("tmdb", {})
    return template.format(
        series_name=bgm.get("series_name") or sub.get("name", ""),
        bangumi_title=bgm.get("subject_name") or sub.get("name", ""),
        bgm_season=bgm.get("season", 1),
        tvdb_season=tvdb.get("season") or bgm.get("season", 1),
        tmdb_season=tmdb.get("season") or bgm.get("season", 1),
        sort=bangumi_sort or sort,
        bangumi_sort=bangumi_sort or sort,
        bangumi_ep=bangumi_ep or bangumi_sort or sort,
        tvdb_episode=tvdb_episode or bangumi_ep or bangumi_sort or sort,
        tmdb_episode=tmdb_episode or bangumi_sort or sort,
    ) + ext


async def write_episode_files(
    tmdb_ep: dict,
    *,
    season_number: int,
    episode_number: int,
    bangumi_ep_id: int | None,
    show_name: str,
    original_name: str,
    bangumi_subject_name: str,
    studios: list[str] | None = None,
    rating: float = 0.0,
    output_dir: str = ".",
    thumb_source: str = "tmdb",
    file_stem: str = "",
) -> dict:
    """Download episode thumbnail and generate ``.nfo`` — no API calls.

    All metadata must already be extracted into *tmdb_ep* before
    calling.  Used by both the RSS flow (:func:`generate_metadata`)
    and the torrent batch flow (:func:`generate_metadata_collection`).

    Args:
        tmdb_ep: Normalised dict with keys ``name``, ``overview``,
            ``air_date``, ``runtime``, ``still_path``.
        season_number:  Season number written into the NFO.
        episode_number: Episode number written into the NFO.
        bangumi_ep_id:  Bangumi episode ID (or ``None``).
        show_name:      Show title → ``<showtitle>``.
        original_name:  Original name → ``<originaltitle>``.
        bangumi_subject_name: Bangumi subject name (fallback for file naming).
        studios:        Network / studio names.
        output_dir:     Directory to write NFO and thumbnail into.
        thumb_source:   ``"tvdb"`` or ``"tmdb"`` controls which CDN is used.
        file_stem:      Base filename (without extension) from path template.
            If empty, falls back to ``{bangumi_subject_name} {ep:02d}``.

    Returns:
        ``{"nfo_path": str, "thumb_path": str}``.
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # ── Thumbnail ───────────────────────────────────────────────────
    if file_stem:
        thumb_base = file_stem
    else:
        thumb_base = f"{bangumi_subject_name or show_name} {episode_number:02d}"
    thumb_path = ""
    still = tmdb_ep.get("still_path", "")
    if still:
        try:
            if thumb_source == "tvdb":
                from .image_downloader import download_tvdb_episode_thumb
                thumb_path = await download_tvdb_episode_thumb(
                    still, output_dir, thumb_base,
                ) or ""
            else:
                thumb_path = await download_episode_thumb(
                    still, output_dir, thumb_base,
                ) or ""
        except Exception:
            logger.exception("thumbnail download failed (non-fatal)")

    # ── Episode NFO ─────────────────────────────────────────────────
    nfo_path = generate_episode_nfo(
        show_name=show_name,
        original_name=original_name,
        episode_name=tmdb_ep.get("name", ""),
        plot=tmdb_ep.get("overview", ""),
        air_date=tmdb_ep.get("air_date", ""),
        runtime=tmdb_ep.get("runtime", 0),
        season_number=season_number,
        episode_number=episode_number,
        bangumi_ep_id=bangumi_ep_id,
        bangumi_subject_name=bangumi_subject_name or show_name,
        directors=tmdb_ep.get("directors", []),
        writers=tmdb_ep.get("writers", []),
        actors=tmdb_ep.get("guest_stars", []),
        thumb_path=Path(thumb_path).name if thumb_path else "",
        studios=studios or [],
        rating=rating,
        output_dir=output_dir,
        tvdb_ep_id=tmdb_ep.get("tvdb_ep_id", 0),
        file_stem=file_stem,
    )

    return {"nfo_path": nfo_path, "thumb_path": thumb_path}


# Simple in-memory caches
async def generate_metadata(
    qb_client, info_hash: str,
    bangumi_id: int, sort: int, bgm_subject_id: int,
    tmdb_id: int, show_name: str, old_torrent_path: str, guid: str,
    bgm_season: int = 1,
    tmdb_season: int | None = None,
    tmdb_ep_offset: int = 0,
    tvdb_id: int = 0,
    tvdb_season: int | None = None,
    tvdb_ep_offset: int = 0,
    tvdb_ep: int = 0,
    season_dir: str = "",
    show_dir: str = "",
    bgm_subject_name: str = "",
    series_name: str = "",
) -> bool:
    """Generate NFO files, download images, and rename in qBittorrent.

    *season_dir* is the directory for episode NFO / thumbnails (also
    used for season.nfo).  *show_dir* is its parent — used for
    tvshow.nfo and show-level images.  Both are derived from the
    download path template.
    """
    _season_dir = Path(season_dir)
    _show_dir = Path(show_dir)

    # ── Read override from download history ────────────────────────
    from ..data import get_all_episodes
    overrides = get_all_episodes(bangumi_id).get(str(sort), {})
    override_tmdb_ep = overrides.get("tmdb_ep")       # None if not set
    override_tmdb_season = overrides.get("tmdb_season")  # None if not set

    # ── TVDB: fetch episode data ───────────────────────────────────
    tvdb_ep_data = None
    tvdb_ep_id = 0
    ep_rating = 0.0
    # Saved for season.nfo reuse (season title, first episode air date)
    tvdb_season_info: dict | None = None
    tvdb_series_data: dict | None = None
    # Episode number variables for path template (initialised to sort as fallback)
    bangumi_ep_val = sort
    bgm_ep_name = ""
    bgm_ep_name_cn = ""
    tmdb_target_ep = 0
    if tvdb_id:
        try:
            from ..clients.tvdb import (
                get_series_extended as tvdb_get_series_extended,
                get_season_extended as tvdb_get_season_extended,
            )

            # Use Bangumi ``ep`` (canonical episode number) + split-season
            # offset for TVDB alignment (TVDB always starts at 1 per season).
            tvdb_ep_offset_val = tvdb_ep_offset or 0
            try:
                eps = await _get_bangumi_episodes(bangumi_id)
                for e in eps:
                    if (e.get("sort") or e.get("ep", 0)) == sort:
                        bangumi_ep_val = e.get("ep") or sort
                        bgm_ep_name = (e.get("name") or "").strip()
                        bgm_ep_name_cn = (e.get("name_cn") or "").strip()
                        break
            except Exception:
                pass
            if tvdb_ep < 1:
                tvdb_ep = 1

            target_tvdb_season = tvdb_season or 1
            logger.info(
                "fetching TVDB series=%d season=%d ep=%d (bgm_sort=%d, offset=%d)",
                tvdb_id, target_tvdb_season, tvdb_ep,
                bangumi_ep_val, tvdb_ep_offset_val,
            )

            # Step 1: Get series extended → find season ID
            series_resp = await tvdb_get_series_extended(tvdb_id)
            series_data = series_resp.json().get("data", series_resp.json())
            tvdb_series_data = series_data  # save for season.nfo
            season_id = None
            for s in series_data.get("seasons", []):
                if s.get("number") == target_tvdb_season:
                    season_id = s.get("id")
                    break
            if not season_id and series_data.get("seasons"):
                # Fallback: use first season
                season_id = series_data["seasons"][0].get("id")

            if season_id:
                # Step 2: Get season extended → find target episode
                season_resp = await tvdb_get_season_extended(season_id)
                season_info = season_resp.json().get("data", season_resp.json())
                tvdb_season_info = season_info  # save for season.nfo
                for ep in season_info.get("episodes", []):
                    if ep.get("number") == tvdb_ep:
                        tvdb_ep_id = ep.get("id")
                        tvdb_ep_data = {
                            "name": ep.get("name", ""),
                            "overview": ep.get("overview", ""),
                            "air_date": ep.get("airDate") or ep.get("aired", ""),
                            "runtime": ep.get("runtime", 0),
                            "still_path": ep.get("image", ""),
                            "tvdb_ep_id": tvdb_ep_id,
                        }
                        ep_rating = ep.get("siteRating", 0) or 0
                        break
        except Exception:
            logger.exception("TVDB episode fetch failed")

    if not tvdb_ep_data:
        logger.error("TVDB episode data not available for S%d E%d, skipping NFO",
                     tvdb_season or 1, tvdb_ep)
        return False

    tmdb_ep = tvdb_ep_data

    # ── Bangumi original Japanese name for <originaltitle> ─────────
    bgm_original_name = ""
    try:
        subject_for_name = await get_subject(bgm_subject_id)
        bgm_original_name = (subject_for_name.get("name") or "").strip()
    except Exception:
        logger.warning("Bangumi subject lookup for original name failed (non-fatal)")

    # ── Season directory ──────────────────────────────────────────
    _season_dir.mkdir(parents=True, exist_ok=True)

    # ── Episode thumb + NFO ───────────────────────────────────────
    # Override episode title from Bangumi (Chinese names)
    if bgm_ep_name_cn:
        tmdb_ep["name"] = bgm_ep_name_cn

    # Compute file stem from path template (same as media file)
    _tmpl = config.RSS_PATH_TEMPLATE
    _stem_sub = {
        "name": show_name,
        "bgm": {
            "series_name": series_name or show_name,
            "subject_name": bgm_subject_name or show_name,
            "season": bgm_season,
        },
        "tvdb": {"season": tvdb_season},
        "tmdb": {"season": tmdb_season},
    }
    _stem_path = format_download_path(
        _tmpl, _stem_sub, sort=sort, ext="",
        bangumi_sort=sort, bangumi_ep=int(bangumi_ep_val),
        tvdb_episode=tvdb_ep, tmdb_episode=tmdb_target_ep,
    )
    file_stem = Path(_stem_path).stem

    # Look up the Bangumi episode ID from the cached episode list
    bgm_ep_id = await _get_bangumi_ep_id(bangumi_id, sort)
    result = await write_episode_files(
        tmdb_ep,
        season_number=tvdb_season or bgm_season,
        episode_number=tvdb_ep,
        bangumi_ep_id=bgm_ep_id,
        show_name=show_name,
        original_name=bgm_ep_name or bgm_original_name or show_name,
        bangumi_subject_name=bgm_subject_name or show_name,
        rating=ep_rating,
        output_dir=str(_season_dir),
        thumb_source="tvdb" if tvdb_id and tvdb_ep_data else "tmdb",
        file_stem=file_stem,
    )
    logger.info("episode NFO: %s", result["nfo_path"])

    # ── Show-level NFO + images (only once) ───────────────────────
    tvshow_nfo = _show_dir / "tvshow.nfo"
    if not tvshow_nfo.exists() and tmdb_id:
        try:
            detail = await tmdb_service.get_tv_show_detail(tmdb_id)
            generate_tv_show_nfo(
                title=detail.get("name", show_name),
                original_title=detail.get("original_name", show_name),
                plot=detail.get("overview", ""),
                premiered=detail.get("first_air_date", ""),
                genres=detail.get("genres", []),
                studios=detail.get("studios", []),
                rating=detail.get("vote_average", 0),
                status=detail.get("status", ""),
                output_dir=str(_show_dir),
                tvdb_id=tvdb_id,
            )
            logger.info("tvshow.nfo generated")
            await download_show_images(tmdb_id, str(_show_dir))
        except Exception:
            logger.exception("tvshow.nfo failed")

    # ── Season NFO ────────────────────────────────────────────────
    season_nfo = _season_dir / "season.nfo"
    if not season_nfo.exists():
        try:
            season_title = show_name
            season_plot = ""
            season_premiered = ""

            if tvdb_season_info:
                # Premiered: first episode air date of this season
                eps = tvdb_season_info.get("episodes", [])
                if eps:
                    first_air = (eps[0].get("airDate") or eps[0].get("aired", ""))
                    if first_air:
                        season_premiered = first_air

            # Still download Bangumi poster
            subject = await get_subject(bgm_subject_id)
            season_title = (subject.get("name_cn") or subject.get("name") or show_name).strip()
            effective_season = tvdb_season or tmdb_season or bgm_season
            poster = await download_season_poster(subject, str(_show_dir), effective_season)
            if poster:
                logger.info("Season %d poster downloaded", effective_season)

            generate_season_nfo(
                title=season_title,
                original_title=subject.get("name", ""),
                plot=season_plot or subject.get("summary", ""),
                premiered=season_premiered or subject.get("date", ""),
                season_number=tvdb_season or bgm_season,
                bangumi_id=bgm_subject_id,
                output_dir=str(_season_dir),
            )
            logger.info("Season %d season.nfo generated", effective_season)
        except Exception:
            logger.exception("season.nfo failed")

    # ── Rename in qBittorrent ─────────────────────────────────────
    ext = Path(old_torrent_path).suffix
    new_path = format_download_path(
        _tmpl, _stem_sub, sort=sort, ext=ext,
        bangumi_sort=sort, bangumi_ep=int(bangumi_ep_val),
        tvdb_episode=tvdb_ep, tmdb_episode=tmdb_target_ep,
    )
    try:
        await rename_file(qb_client, info_hash, old_torrent_path, new_path)
        logger.info("renamed: %s → %s", old_torrent_path, new_path)
    except Exception:
        logger.exception("rename failed")

    return True


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
    if primary_rss:
        primary_items = await _fetch_passed_items(
            primary_rss, filter_tags, bangumi_id,
            extra_exclude_patterns=primary_exclude, source="primary",
            bgm_sortrange=bgm_sortrange, air_date=air_date,
        )
        new_downloads = 0
        for item in primary_items:
            if await _download_item(item, bangumi_id, "primary", sub):
                new_downloads += 1
    else:
        new_downloads = 0

    # 2. Always check backup RSS — it may have episodes the primary doesn't
    backup_url = backup.get("rss_url", "")
    if backup_url:
        backup_tags = backup.get("filter_tags")
        if backup_tags is None:
            backup_tags = filter_tags
        backup_exclude = backup.get("exclude_patterns") or []
        backup_items = await _fetch_passed_items(
            backup_url, backup_tags, bangumi_id,
            extra_exclude_patterns=backup_exclude, source="backup",
            bgm_sortrange=bgm_sortrange, air_date=air_date,
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

    # Pre-fetch episodes (cached) so we can match rss_ep → sort for dedup
    episodes = await _get_bangumi_episodes(bangumi_id)

    # ── Sort RSS items by pub_date (earliest first) ──
    feed["items"].sort(key=lambda item: item.get("pub_date") or "9999")

    # ── Track covered sorts (already downloaded) for sortrange limit ──
    downloaded_sorts: set[int] = set()
    for ep_sort_str in get_all_episodes(bangumi_id):
        try:
            downloaded_sorts.add(int(ep_sort_str))
        except (ValueError, TypeError):
            pass
    covered: set[int] = set(downloaded_sorts)

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

        # ── Assign sort: map RSS episode number to Bangumi sort ──
        # Use _match_rss_ep_to_sort first for a deterministic rss_ep → sort
        # mapping.  Only fall back to sequential fill when the mapped sort
        # falls outside bgm_sortrange (e.g. RSS episode numbering doesn't
        # align with Bangumi's sort order, or the episode belongs to a
        # different season that happens to appear in this feed).
        sort = _match_rss_ep_to_sort(episodes, rss_ep)
        if bgm_sortrange and bgm_sortrange[0] > 0:
            if sort > bgm_sortrange[1]:
                # Mapped sort above range — likely a different season, skip
                continue
            if sort < bgm_sortrange[0]:
                # Mapped sort below range — sequential fill as fallback
                fallback = 0
                for s in range(bgm_sortrange[0], bgm_sortrange[1] + 1):
                    if s not in covered:
                        fallback = s
                        break
                if fallback:
                    sort = fallback
        item["sort"] = sort

        # ── Sort-range duplicate filter ──
        if sort in covered:
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
        enriched = await enrich_subscription(bangumi_id)
        if enriched:
            sub.update(enriched)
            from ..data import update_subscription
            update_subscription(bangumi_id, enriched)
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
    # Prefer the sort already assigned by _fetch_passed_items (sequential
    # fill of bgm_sortrange); fall back to legacy matching.
    sort = item.get("sort") or 0
    if not sort:
        episodes = await _get_bangumi_episodes(bgm_subject_id)
        sort = _match_rss_ep_to_sort(episodes, rss_ep_num)
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

    # ── v2 replacement: delete old torrent from qBittorrent ─────────
    item_pub_date = item.get("pub_date", "")
    existing_source = get_episode_source(bangumi_id, sort)
    if existing_source and item_pub_date:
        existing_pub = get_episode_pub_date(bangumi_id, sort)
        if existing_pub and item_pub_date > existing_pub:
            # Fetch old info_hash to delete the old torrent
            old_entries = get_all_episodes(bangumi_id)
            old_entry = old_entries.get(str(sort))
            if old_entry and old_entry.get("info_hash"):
                old_hash = old_entry["info_hash"]
                try:
                    qb = await qb_login(config.QBITTORRENT_URL, config.QBITTORRENT_USERNAME, config.QBITTORRENT_PASSWORD)
                    await delete_torrent(qb, old_hash, delete_files=False)
                    remove_episode_record(bangumi_id, sort)
                    print(f"         🗑️ 删除旧种子 [{old_hash[:12]}…]，替换为 v2")
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
    series_name = bgm.get("series_name") or show_name
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
