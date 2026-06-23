"""RSS download worker — poll subscriptions, download new episodes via qBittorrent."""

import asyncio
import tempfile
import traceback
from pathlib import Path

import httpx

from .. import config
from ..clients.qbittorrent import login as qb_login, add_torrent, resume_torrent, delete_torrent
from ..clients.qbittorrent import (
    login as qb_login, add_torrent, rename_file, resume_torrent,
)
from ..clients.bangumi import get_episodes as bgm_get_episodes, get_subject
from ..data import (
    get_tmdb_id, get_tmdb_season, get_bangumi_name,
    list_subscriptions, mark_downloaded, get_episode_source,
    get_episode_pub_date, remove_episode_record,
    get_all_episodes,
)
from . import rss as rss_service, tmdb as tmdb_service, bangumi as bangumi_service
from .nfo_generator import generate_episode_nfo, generate_tv_show_nfo, generate_season_nfo
from .image_downloader import download_episode_thumb, download_show_images, download_season_poster
from ..utils.torrent_hash import compute_info_hash

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
# Episode offset — map RSS episode numbers to Bangumi sort range
# ═══════════════════════════════════════════════════════════════════════

_bgm_ep_cache: dict[int, list[dict]] = {}  # subject_id → episodes
_bgm_chain_cache: dict[int, list[dict]] = {}  # subject_id → chain


async def _get_bangumi_episodes(subject_id: int) -> list[dict]:
    """Get episodes for a Bangumi subject (cached)."""
    eps = _bgm_ep_cache.get(subject_id)
    if not eps:
        try:
            eps = await bgm_get_episodes(subject_id)
            _bgm_ep_cache[subject_id] = eps
        except Exception:
            return []
    return eps


def _match_rss_ep_to_sort(episodes: list[dict], rss_ep: int) -> int:
    """Match RSS episode number to Bangumi sort value.

    Tries exact match on 'ep' field first, then positional fallback
    (rss_ep-th episode in the sorted list). Returns the sort value,
    or rss_ep as-is if no match found.
    """
    # Exact match on ep field
    for e in episodes:
        if e.get("ep") == rss_ep:
            return e.get("sort") or rss_ep
    # Positional fallback (1-based → 0-based index)
    if 0 < rss_ep <= len(episodes):
        e = episodes[rss_ep - 1]
        return e.get("sort") or rss_ep
    return rss_ep


async def enrich_subscription(bangumi_id: int) -> dict | None:
    """Build chain + sort range + TMDB info for a subscription.

    Called once when a subscription is added (or lazily when an
    existing subscription is first downloaded).  Returns fields
    to write into subscriptions.json.

    Returns:
        dict with bgm_season, bgm_sortrange, tmdb_id, tmdb_season,
        or None on failure.
    """
    try:
        print(f"   🔗 丰富化订阅信息 (bgm_id={bangumi_id})...")

        # 1. Build Bangumi chain
        first_id = await bangumi_service.find_first_in_chain(bangumi_id)
        chain, _ = await bangumi_service.build_bangumi_chain(first_id)
        if not chain:
            print(f"   ⚠️ 无法构建 Bangumi 链")
            return None

        # Cache chain under every subject ID in it
        for entry in chain:
            _bgm_chain_cache[entry["id"]] = chain

        # 2. Find position in chain → bgm_season
        bgm_season = 1
        for i, entry in enumerate(chain):
            if entry["id"] == bangumi_id:
                bgm_season = i + 1
                break
        print(f"   ✅ bgm_season={bgm_season}")

        # 3. Get sort range
        eps = await _get_bangumi_episodes(bangumi_id)
        sorts = [e.get("sort") or e.get("ep", 0) for e in eps]
        bgm_sortrange = [min(sorts), max(sorts)] if sorts else [0, 0]
        print(f"   ✅ bgm_sortrange={bgm_sortrange}")

        # 4. TMDB info from bangumi_mikan_map.json
        tmdb_id = get_tmdb_id(bangumi_id)
        tmdb_season = get_tmdb_season(bangumi_id)

        return {
            "bgm_season": bgm_season,
            "bgm_sortrange": bgm_sortrange,
            "tmdb_id": tmdb_id or 0,
            "tmdb_season": tmdb_season,
        }
    except Exception as e:
        print(f"   ⚠️ enrich_subscription 失败: {e}")
        traceback.print_exc()
        return None


# ═══════════════════════════════════════════════════════════════════════
# NFO & file structure generation
# ═══════════════════════════════════════════════════════════════════════

async def _generate_metadata(
    qb_client, info_hash: str,
    bangumi_id: int, sort: int, bgm_subject_id: int,
    tmdb_id: int, show_name: str, old_torrent_path: str, guid: str,
    bgm_season: int = 1,
    tmdb_season: int | None = None,
    base_path: str = "",
    sub_path: str = "",
):
    """Generate NFO files, download images, and rename in qBittorrent.

    *base_path* + *sub_path* form the season directory (e.g.
    ``/Media/番剧/冰之城墙/Season 1``).  The show directory is the
    parent of the season directory.
    """
    from ..clients.tmdb import get_season_detail as tmdb_get_season

    season_dir = Path(base_path) / sub_path if base_path else Path(config.QBITTORRENT_SAVE_PATH) / show_name / f"Season {bgm_season}"
    show_dir = season_dir.parent

    # ── TMDB: fetch the single target season ──────────────────────
    target_tmdb_season = tmdb_season or bgm_season
    print(f"         📡 获取 TMDB S{target_tmdb_season} 集数数据 (tmdb_id={tmdb_id})...")
    try:
        resp = await tmdb_get_season(tmdb_id, target_tmdb_season)
        season_data = resp.json()
    except Exception as e:
        print(f"         ⚠️ TMDB season API 失败: {e}")
        return

    # Build tmdb_ep dict from the matching episode (by sort)
    tmdb_ep = None
    for ep in (season_data.get("episodes") or []):
        if ep.get("episode_number") == sort:
            # Extract crew
            directors = [c["name"] for c in ep.get("crew", []) if c.get("job") == "Director"]
            writers = [c["name"] for c in ep.get("crew", []) if c.get("job") == "Writer"]
            guest_stars = [
                {"name": gs["name"], "character": gs.get("character", "")}
                for gs in ep.get("guest_stars", [])
            ]
            tmdb_ep = {
                "epNum": ep["episode_number"],
                "name": ep.get("name", ""),
                "tmdbId": ep["id"],
                "overview": ep.get("overview", ""),
                "airDate": ep.get("air_date", ""),
                "runtime": ep.get("runtime", 0),
                "stillPath": ep.get("still_path", ""),
                "voteAverage": ep.get("vote_average", 0),
                "directors": directors,
                "writers": writers,
                "guestStars": guest_stars,
            }
            break

    if not tmdb_ep:
        print(f"         ⚠️ TMDB S{target_tmdb_season} 中未找到 sort={sort} 的剧集，跳过 NFO 生成")
        return

    # ── Season directory ──────────────────────────────────────────
    season_dir.mkdir(parents=True, exist_ok=True)

    # ── Episode thumb ─────────────────────────────────────────────
    thumb_path = ""
    still = tmdb_ep.get("stillPath", "")
    if still:
        try:
            thumb_path = await download_episode_thumb(
                still, str(season_dir), f"{show_name} {sort:02d}",
            ) or ""
        except Exception:
            pass

    # ── Episode NFO ───────────────────────────────────────────────
    ep_path = generate_episode_nfo(
        tmdb_show_name=show_name,
        tmdb_ep_name=tmdb_ep.get("name", ""),
        tmdb_ep_overview=tmdb_ep.get("overview", ""),
        tmdb_ep_air_date=tmdb_ep.get("airDate", ""),
        tmdb_ep_runtime=tmdb_ep.get("runtime", 0),
        tmdb_ep_id=tmdb_ep.get("tmdbId", 0),
        season_number=bgm_season,
        episode_number=sort,
        bangumi_ep_id=None,
        tmdb_original_name=show_name,
        bangumi_subject_name=show_name,
        directors=tmdb_ep.get("directors", []),
        writers=tmdb_ep.get("writers", []),
        actors=tmdb_ep.get("guestStars", []),
        thumb_path=Path(thumb_path).name if thumb_path else "",
        output_dir=str(season_dir),
    )
    print(f"         📄 {ep_path}")

    # ── Show-level NFO + images (only once) ───────────────────────
    tvshow_nfo = show_dir / "tvshow.nfo"
    if not tvshow_nfo.exists():
        try:
            detail = await tmdb_service.get_tv_show_detail(tmdb_id)
            generate_tv_show_nfo(
                title=detail.get("name", show_name),
                original_title=detail.get("original_name", show_name),
                plot=detail.get("overview", ""),
                premiered=detail.get("first_air_date", ""),
                tmdb_id=tmdb_id,
                genres=detail.get("genres", []),
                studios=detail.get("studios", []),
                rating=detail.get("vote_average", 0),
                status=detail.get("status", ""),
                output_dir=str(show_dir),
            )
            print(f"         📄 tvshow.nfo")
            await download_show_images(tmdb_id, str(show_dir))
        except Exception as e:
            print(f"         ⚠️ tvshow.nfo 失败: {e}")

    # ── Season NFO ────────────────────────────────────────────────
    season_nfo = season_dir / "season.nfo"
    if not season_nfo.exists():
        try:
            subject = await get_subject(bgm_subject_id)
            poster = await download_season_poster(subject, str(show_dir), bgm_season)
            if poster:
                print(f"         🖼️ Season {bgm_season} poster")
            generate_season_nfo(
                title=subject.get("name_cn") or subject.get("name", ""),
                original_title=subject.get("name", ""),
                plot=subject.get("summary", ""),
                premiered=subject.get("date", ""),
                season_number=bgm_season,
                bangumi_id=bgm_subject_id,
                output_dir=str(season_dir),
            )
            print(f"         📄 Season {bgm_season}/season.nfo")
        except Exception as e:
            print(f"         ⚠️ season.nfo 失败: {e}")

    # ── Rename in qBittorrent ─────────────────────────────────────
    ext = Path(old_torrent_path).suffix
    new_path = f"{sub_path}/{show_name} {sort:02d}{ext}"
    try:
        await rename_file(qb_client, info_hash, old_torrent_path, new_path)
        print(f"         📂 {old_torrent_path} → {new_path}")
    except Exception as e:
        print(f"         ⚠️ 重命名失败: {e}")


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

    filter_tags = sub.get("filter_tags") or []
    name = sub.get("name", str(bangumi_id))

    # 1. Try primary RSS
    primary_items = await _fetch_passed_items(sub["rss_url"], filter_tags, bangumi_id)
    new_downloads = 0
    for item in primary_items:
        if await _download_item(item, bangumi_id, "primary", sub):
            new_downloads += 1

    # 2. If primary had nothing new, try backup RSS
    backup_url = sub.get("backup_rss_url", "")
    if new_downloads == 0 and backup_url:
        backup_tags = sub.get("backup_filter_tags") or filter_tags
        backup_items = await _fetch_passed_items(backup_url, backup_tags, bangumi_id)
        for item in backup_items:
            if await _download_item(item, bangumi_id, "backup", sub):
                new_downloads += 1

    if new_downloads > 0:
        print(f"   📥 {name}: {new_downloads} 新集")
        _worker_status["downloaded"] += new_downloads


async def _check_completion(bangumi_id: int, sub: dict):
    """If all episodes in bgm_sortrange are downloaded, mark active=0."""
    bgm_sortrange = sub.get("bgm_sortrange", [0, 0])
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


async def _fetch_passed_items(rss_url: str, filter_tags: list[str], bangumi_id: int) -> list[dict]:
    """Fetch RSS and return items that pass filter AND aren't downloaded yet.

    Uses Bangumi sort (not raw RSS episode number) for dedup, so the
    dedup key matches what ``mark_downloaded`` writes.
    """
    try:
        feed = await rss_service.fetch_and_parse_rss(rss_url, filter_tags, bangumi_id)
    except Exception as e:
        print(f"   ⚠️ RSS 获取失败: {e}")
        return []

    # Pre-fetch episodes (cached) so we can match rss_ep → sort for dedup
    episodes = await _get_bangumi_episodes(bangumi_id)

    candidates = []
    for item in feed["items"]:
        if not item["passed"] or item["excluded"]:
            continue
        rss_ep = item.get("episode_number") or 0
        if not rss_ep:
            continue

        sort = _match_rss_ep_to_sort(episodes, rss_ep)
        source = get_episode_source(bangumi_id, sort)
        item_pub_date = item.get("pub_date", "")
        if source == "primary":
            # Same sort already downloaded via primary — check for v2 replacement
            existing_pub = get_episode_pub_date(bangumi_id, sort)
            if item_pub_date and existing_pub:
                if item_pub_date > existing_pub:
                    # Newer pub_date → v2/修正版, allow replacement
                    print(f"      🔄 EP{rss_ep:02d} v2 detected: {item_pub_date} > {existing_pub}")
                else:
                    continue  # older or same date, skip
            else:
                continue  # can't compare pub_date, skip (treat as same version)
        candidates.append(item)
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

    # ── Ensure subscription has enrichment fields ──────────────────
    bgm_season = sub.get("bgm_season")
    if bgm_season is None:
        print(f"         🔗 订阅缺少 bgm_season，正在丰富化...")
        enriched = await enrich_subscription(bangumi_id)
        if enriched:
            sub.update(enriched)
            from ..data import update_subscription
            update_subscription(bangumi_id, enriched)
        else:
            bgm_season = 1  # fallback

    bgm_season = sub.get("bgm_season", 1)
    tmdb_season = sub.get("tmdb_season")

    # ── Match RSS episode to Bangumi sort ──────────────────────────
    episodes = await _get_bangumi_episodes(bgm_subject_id)
    sort = _match_rss_ep_to_sort(episodes, rss_ep_num)
    if sort != rss_ep_num:
        print(f"         📐 rss_ep={rss_ep_num} → sort={sort}")
    bgm_sortrange = sub.get("bgm_sortrange", [0, 0])
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
    proxy = None
    if config.PROXY_HOST:
        proxy = f"http://{config.PROXY_HOST}:{config.PROXY_PORT}"
    try:
        async with httpx.AsyncClient(proxy=proxy, timeout=60.0, follow_redirects=True) as client:
            resp = await client.get(torrent_url)
            resp.raise_for_status()
    except Exception as e:
        print(f"      ❌ 下载 .torrent 失败: {e}")
        return False

    with tempfile.NamedTemporaryFile(suffix=".torrent", delete=False) as f:
        f.write(resp.content)
        tmp_path = f.name

    # ── Compute info-hash from the .torrent file ───────────────────
    torrent_hash = compute_info_hash(tmp_path)

    # ── Compute download paths ─────────────────────────────────────
    show_name = sub.get("name", str(bangumi_id))
    rss_base = config.RSS_DOWNLOAD_PATH or config.QBITTORRENT_SAVE_PATH
    sub_path_template = sub.get("download_path", f"/{show_name}/Season {{season}}")
    sub_path = sub_path_template.format(show_name=show_name, season=bgm_season).strip("/")

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
    if tmdb_id:
        try:
            from ..clients.qbittorrent import get_torrent_files
            files = await get_torrent_files(qb, info_hash)
            old_path = files[0]["name"] if files else guid
            await _generate_metadata(
                qb, info_hash, bangumi_id, sort,
                bgm_subject_id, tmdb_id, show_name,
                old_path, guid,
                bgm_season=bgm_season,
                tmdb_season=tmdb_season,
                base_path=rss_base,
                sub_path=sub_path,
            )
        except Exception as e:
            print(f"      ⚠️ NFO 生成失败: {e}")

    # ── Resume download ────────────────────────────────────────────
    try:
        await resume_torrent(qb, info_hash)
    except Exception:
        pass  # resume might fail if auto-started

    mark_downloaded(bangumi_id, sort, item.get("rss_url", ""), guid, source,
                    pub_date=item.get("pub_date", ""), info_hash=torrent_hash)

    # Check if all episodes in the sort range are now downloaded
    await _check_completion(bangumi_id, sub)

    return True
