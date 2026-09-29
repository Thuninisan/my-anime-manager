"""Alternative torrent search flow for ktnbytes / 343-Labs releases.

These releases have known-good titles parsed by anitopy — we search TMDB
directly, then pull episode data from TMDB + related Bangumi + TVDB
entries discovered through the mapping table.
"""

import asyncio
import logging
from collections import Counter
from pathlib import Path

from .preview import _parse_file, _deduplicate_show_names, SKIP_EXTENSIONS
from ...utils.torrent_file_reader import read_torrent_file_list, read_torrent_name
from ...clients import tmdb as tmdb_client
from ...clients import bangumi as bgm_client
from .. import tmdb as tmdb_service
from ..tvdb import fetch_tvdb_series_episodes
from ... import data as data_store
from ... import config

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# TMDB search helper
# ═══════════════════════════════════════════════════════════════════════

async def _search_tmdb_single(name: str) -> dict | None:
    """Search in Chinese and return the first confirmed Animation TV result.

    Args:
        name: Show name to search for.

    Returns:
        dict with ``id, name, original_name, first_air_date, overview``,
        or ``None`` if no result was found.
    """
    res = await tmdb_client.search_tv(name, language="zh-CN")
    results = tmdb_service.filter_animation_candidates(
        res.json().get("results", []), name,
    )

    if not results:
        logger.warning("TMDB 未找到可确认的动画作品: %r", name)
        return None

    first = results[0]
    return {
        "id": first["id"],
        "name": first.get("name", ""),
        "original_name": first.get("original_name", ""),
        "first_air_date": first.get("first_air_date", ""),
        "overview": first.get("overview", ""),
    }


async def _search_tmdb_movie(name: str) -> dict | None:
    """Search TMDB for an Animation movie with Chinese-language results.

    Uses ``language="zh-CN"`` directly so the returned title is the
    Chinese name — no need for a second round-trip.

    Args:
        name: Movie name to search for.

    Returns:
        dict with ``id, name, original_name, release_date, overview``,
        or ``None`` if no result was found.
    """
    res = await tmdb_client.search_movie(name, language="zh-CN")
    results = tmdb_service.filter_animation_candidates(
        res.json().get("results", []), name,
    )

    if not results:
        logger.warning("TMDB 未找到可确认的动画电影: %r", name)
        return None

    first = results[0]
    return {
        "id": first["id"],
        "name": first.get("title", ""),
        "original_name": first.get("original_title", ""),
        "release_date": first.get("release_date", ""),
        "overview": first.get("overview", ""),
        "media_type": "movie",
    }


# TVDB episode fetching — delegated to services.tvdb.fetch_tvdb_series_episodes


# ═══════════════════════════════════════════════════════════════════════
# Bangumi episode fetching (reuses pattern from torrent_preview.py)
# ═══════════════════════════════════════════════════════════════════════

async def _fetch_bangumi_episodes(bgm_id: int) -> dict | None:
    """Fetch episode list + subject name for a Bangumi entry.

    Args:
        bgm_id: Bangumi subject ID.

    Returns:
        ``{name, episodes: [{sort, id, name, name_cn?}]}`` or ``None``.
    """
    try:
        subject = await bgm_client.get_subject(bgm_id)
        name = subject.get("name_cn") or subject.get("name", str(bgm_id))
    except Exception:
        logger.exception("torrent.tmdb_first bangumi_subject_failed bangumi_id=%s", bgm_id)
        name = str(bgm_id)

    try:
        raw_eps = await bgm_client.get_episodes(bgm_id, ep_type=None)
        eps = [e for e in raw_eps if e.get("type") in (0, 1)]
        logger.info(
            "torrent.tmdb_first bangumi_episodes bangumi_id=%s raw=%d kept=%d",
            bgm_id, len(raw_eps), len(eps),
        )
        if not eps:
            logger.warning("torrent.tmdb_first bangumi_episodes_empty bangumi_id=%s", bgm_id)
    except Exception:
        logger.exception("torrent.tmdb_first bangumi_episodes_failed bangumi_id=%s", bgm_id)
        eps = []

    clean_eps = []
    for ep in eps:
        entry = {
            "sort": ep.get("sort") or ep.get("ep", 0),
            "id": ep["id"],
            "name": ep.get("name", ""),
        }
        cn = ep.get("name_cn")
        if cn and cn != entry["name"]:
            entry["name_cn"] = cn
        clean_eps.append(entry)
    clean_eps.sort(key=lambda x: x["sort"])

    return {"name": name, "episodes": clean_eps}


# ═══════════════════════════════════════════════════════════════════════
# Episode data orchestration for a single TMDB ID
# ═══════════════════════════════════════════════════════════════════════

async def _fetch_all_episode_data(tmdb_id: int) -> dict:
    """Fetch episode data from TMDB + related Bangumi + TVDB sources.

    Looks up ``bangumi_mikan_map.json`` for all entries linked to the
    same *tmdb_id*, then fetches episode listings from every source
    concurrently where possible.

    Args:
        tmdb_id: TMDB series ID.

    Returns:
        ``{tmdb, bangumi, tvdb, map_entries}`` — each source key maps to
        a dict keyed by source-specific ID.
    """
    # ── TMDB season map (kick off first — no dependency) ──
    tmdb_task = asyncio.create_task(
        tmdb_service.build_season_episode_map(tmdb_id)
    )

    # ── Map lookup (sync, fast) ──
    map_entries = data_store.get_map_entries_by_tmdb_id(tmdb_id)
    logger.info("torrent.tmdb_first map_lookup tmdb_id=%s entries=%d", tmdb_id, len(map_entries))
    if not map_entries:
        logger.warning(
            "torrent.tmdb_first map_missing tmdb_id=%s; skipping Bangumi/TVDB requests (no name-search fallback)",
            tmdb_id,
        )
    if map_entries:
        logger.debug(f"   🗺 map.json: {len(map_entries)} 个关联条目 (TMDB {tmdb_id})")
        for me in map_entries:
            tvdb_str = f"tvdb={me['tvdb_id']}" if me.get("tvdb_id") else "tvdb=N/A"
            logger.debug(f"     - Bangumi {me['bangumi_id']} ({me['name']})  {tvdb_str}")

    # ── Collect unique Bangumi IDs ──
    bangumi_ids: list[int] = sorted({me["bangumi_id"] for me in map_entries})

    # ── Collect unique TVDB IDs ──
    tvdb_ids: list[int] = sorted({
        me["tvdb_id"] for me in map_entries
        if me.get("tvdb_id") is not None
    })
    logger.info(
        "torrent.tmdb_first source_ids tmdb_id=%s bangumi_ids=%s tvdb_ids=%s",
        tmdb_id, bangumi_ids, tvdb_ids,
    )
    if map_entries and not tvdb_ids:
        logger.warning("torrent.tmdb_first tvdb_mapping_missing tmdb_id=%s", tmdb_id)

    # ── Fetch Bangumi episodes (serial via semaphore) ──
    bgm_sem = asyncio.Semaphore(1)
    bangumi_data: dict[str, dict] = {}

    async def _fetch_one_bgm(bid: int):
        async with bgm_sem:
            return str(bid), await _fetch_bangumi_episodes(bid)

    if bangumi_ids:
        bgm_tasks = [_fetch_one_bgm(bid) for bid in bangumi_ids]
        bgm_results = await asyncio.gather(*bgm_tasks, return_exceptions=True)
        for bid, r in zip(bangumi_ids, bgm_results):
            if isinstance(r, BaseException):
                logger.error("torrent.tmdb_first bangumi_fetch_failed tmdb_id=%s bangumi_id=%s",
                             tmdb_id, bid, exc_info=(type(r), r, r.__traceback__))
            elif r[1] is not None:
                bid_str, data = r
                bangumi_data[bid_str] = data
                logger.debug(f"   Bangumi {bid_str} ({data['name']}): {len(data['episodes'])} 集")

    # ── Fetch TVDB episodes (serial via semaphore) ──
    tvdb_sem = asyncio.Semaphore(1)
    tvdb_data: dict[str, dict] = {}

    async def _fetch_one_tvdb(tid: int):
        # Find matching map entry for the name
        match = next((me for me in map_entries if me.get("tvdb_id") == tid), None)
        fallback_name = match["name"] if match else ""
        async with tvdb_sem:
            return str(tid), await fetch_tvdb_series_episodes(tid, series_name=fallback_name)

    if tvdb_ids:
        tvdb_tasks = [_fetch_one_tvdb(tid) for tid in tvdb_ids]
        tvdb_results = await asyncio.gather(*tvdb_tasks, return_exceptions=True)
        for tid, r in zip(tvdb_ids, tvdb_results):
            if isinstance(r, BaseException):
                logger.error("torrent.tmdb_first tvdb_fetch_failed tmdb_id=%s tvdb_id=%s",
                             tmdb_id, tid, exc_info=(type(r), r, r.__traceback__))
            elif r[1] is not None:
                tid_str, data = r
                tvdb_data[tid_str] = data
            else:
                logger.warning("torrent.tmdb_first tvdb_fetch_empty tmdb_id=%s tvdb_id=%s", tmdb_id, tid)

    # ── Await TMDB ──
    try:
        tmdb_season_map = await tmdb_task
    except Exception as exc:
        logger.warning(f"   ⚠️ TMDB {tmdb_id} season map 获取失败: {exc}")
        tmdb_season_map = {}

    # ── Convert TMDB int keys → str for JSON compatibility ──
    tmdb_data: dict[str, dict] = {
        str(sn): sd for sn, sd in tmdb_season_map.items()
    }

    return {
        "tmdb": {str(tmdb_id): tmdb_data},
        "bangumi": bangumi_data,
        "tvdb": tvdb_data,
        "map_entries": map_entries,
    }


# ═══════════════════════════════════════════════════════════════════════
# Top-level entry point
# ═══════════════════════════════════════════════════════════════════════

async def search_by_tmdb(
    torrent_path: str, torrent_name: str = "",
) -> dict:
    """Full pipeline for ktnbytes / 343-Labs torrents.

    Flow:
      1. Read torrent file list (and name if not provided)
      2. anitopy parse each file (reuses ``_parse_file``)
      3. Deduplicate show names
      4. Search TMDB with each show name
      5. Look up map.json for related Bangumi + TVDB IDs
      6. Fetch episode data: TMDB + Bangumi + TVDB
      7. Return structured result

    Args:
        torrent_path: Filesystem path to a .torrent file.
        torrent_name: Optional pre-read torrent name (avoids re-reading).

    Returns:
        Nested dict with ``parsed_files``, ``specials``, ``skipped_files``,
        ``show_names``, ``search_results``, and ``episode_data``.

    Raises:
        RuntimeError: If no files can be parsed from the torrent.
    """
    # ── Step 1: Read torrent ──
    if not torrent_name:
        torrent_name = read_torrent_name(torrent_path)
    logger.info("torrent.tmdb_first start torrent=%r", torrent_name)

    logger.debug("📋 读取种子文件内容 (bencode)...")
    file_list: list[dict] = read_torrent_file_list(torrent_path)
    logger.debug(f"   → {len(file_list)} 个文件")

    # ── Collect subtitle files before anitopy parsing ──
    subtitle_files: list[str] = [
        Path(f["name"]).name
        for f in file_list
        if Path(f["name"]).suffix.lower() in SKIP_EXTENSIONS
    ]
    if subtitle_files:
        logger.debug(f"   📝 {len(subtitle_files)} 个字幕文件")

    # ── Filter subtitle / font-archive files ──
    before_ext = len(file_list)
    file_list = [
        f for f in file_list
        if Path(f["name"]).suffix.lower() not in SKIP_EXTENSIONS
    ]
    ext_skipped = before_ext - len(file_list)
    if ext_skipped:
        logger.debug(f"   📎 非视频文件过滤: {ext_skipped} 个文件 (字幕/字体/音频)")

    # ── Step 2: anitopy parsing ──
    logger.debug("🔧 anitopy 逐文件解析...")
    parsed_results: list[dict] = [_parse_file(f) for f in file_list]

    parsed_files: list[dict] = [r for r in parsed_results if not r["is_extra"]]
    skipped_files: list[dict] = [
        {
            "file_name": r["file_name"],
            "torrent_path": r["torrent_path"],
            "skip_reason": r["skip_reason"],
        }
        for r in parsed_results if r["is_extra"]
    ]

    logger.debug(f"   合规剧集: {len(parsed_files)} 个")
    logger.debug(f"   跳过文件: {len(skipped_files)} 个")
    reason_counts = Counter(s["skip_reason"] for s in skipped_files)
    for reason, count in reason_counts.most_common():
        logger.debug(f"     - {reason}: {count}")

    if not parsed_files:
        raise RuntimeError("没有找到可处理的剧集文件")

    # ── Collect SP/Extra files ──
    specials: list[dict] = [
        {
            "file_name": r["file_name"],
            "torrent_path": r["torrent_path"],
        }
        for r in parsed_results if r["is_extra"]
    ]

    # ── Step 3: Deduplicate show names ──
    show_names: list[str] = _deduplicate_show_names(parsed_files)
    logger.debug(f"📛 去重节目名: {len(show_names)} 个")
    for i, name in enumerate(show_names):
        count = sum(
            1 for p in parsed_files
            if p.get("show_name", "").lower() == name.lower()
        )
        logger.debug(f"   [{i + 1}] {name} ({count} 个文件)")

    # ── Step 4 + 5 + 6: Search TMDB → map lookup → fetch episode data ──
    logger.debug("🔍 TMDB 直搜 + 关联数据获取...")

    # Count files per show name for movie detection
    file_counts: dict[str, int] = {}
    for p in parsed_files:
        sn = p.get("show_name", "")
        file_counts[sn] = file_counts.get(sn, 0) + 1

    search_results: dict = {}
    episode_data: dict = {
        "tmdb": {},
        "bangumi": {},
        "tvdb": {},
    }
    any_movie = False

    for name in show_names:
        file_count = file_counts.get(name, 0)
        is_movie = file_count == 1  # ktnbytes: single file = movie
        logger.info(
            "torrent.tmdb_first search torrent=%r query=%r files=%d media_type=%s",
            torrent_name, name, file_count, "movie" if is_movie else "tv",
        )

        if is_movie:
            any_movie = True
            logger.debug(f'   🎬 搜索电影: "{name}"')
            tmdb_info = await _search_tmdb_movie(name)
        else:
            logger.debug(f'   🔎 搜索: "{name}"')
            tmdb_info = await _search_tmdb_single(name)

        if tmdb_info is None:
            search_results[name] = {
                "tmdb": None,
                "bangumi": None,
                "media_type": "movie" if is_movie else None,
                "map_entries": [],
            }
            continue

        tmdb_id = tmdb_info["id"]
        logger.info(
            "torrent.tmdb_first selected torrent=%r query=%r tmdb_id=%s title=%r original_name=%r",
            torrent_name, name, tmdb_id, tmdb_info["name"], tmdb_info.get("original_name"),
        )

        if is_movie:
            # Movie: reverse lookup map.json for Bangumi ID + name
            map_entries = data_store.get_map_entries_by_tmdb_id(tmdb_id)
            logger.info(
                "torrent.tmdb_first movie_episode_fetch_skipped tmdb_id=%s map_entries=%d; movie branch only resolves mapping",
                tmdb_id, len(map_entries),
            )
            bangumi_ids = sorted({me["bangumi_id"] for me in map_entries})
            bangumi_id = bangumi_ids[0] if bangumi_ids else 0
            bangumi_name = map_entries[0]["name"] if map_entries else ""

            logger.debug(f"   ✅ TMDB 电影 {tmdb_id}: {tmdb_info['name']} ({tmdb_info.get('original_name', '')})")
            if map_entries:
                logger.debug(f"   🗺 map.json: {len(map_entries)} 个关联条目")
                for me in map_entries:
                    logger.debug(f"     - Bangumi {me['bangumi_id']} ({me['name']})")

            search_results[name] = {
                "tmdb": tmdb_info,
                "media_type": "movie",
                "bangumi": {"id": bangumi_id, "name": bangumi_name} if bangumi_id else None,
                "map_entries": map_entries,
            }
        else:
            # TV: existing episode data fetch
            logger.debug(f"   ✅ TMDB {tmdb_id}: {tmdb_info['name']} ({tmdb_info.get('original_name', '')})")

            # Fetch episode data from all sources
            all_data = await _fetch_all_episode_data(tmdb_id)

            # Merge into episode_data
            episode_data["tmdb"].update(all_data["tmdb"])
            episode_data["bangumi"].update(all_data["bangumi"])
            episode_data["tvdb"].update(all_data["tvdb"])

            map_entries = all_data["map_entries"]
            bangumi_ids = sorted({me["bangumi_id"] for me in map_entries})
            tvdb_ids = sorted({
                me["tvdb_id"] for me in map_entries
                if me.get("tvdb_id") is not None
            })

            search_results[name] = {
                "tmdb": tmdb_info,
                "bangumi_ids": bangumi_ids,
                "tvdb_ids": tvdb_ids,
                "map_entries": map_entries,
            }


    return {
        "index": "movie" if any_movie else "tmdb",
        "torrent_name": torrent_name,
        "torrent_path": torrent_path,
        "total_files": len(file_list),
        "subtitles": subtitle_files,
        "parsed_files": [
            {
                "file_name": p["file_name"],
                "torrent_path": p["torrent_path"],
                "show_name": p["show_name"],
                "season": p["season"],
                "episode": p["episode"],
                "parsed": p["parsed"],
            }
            for p in parsed_files
        ],
        "specials": specials,
        "skipped_files": skipped_files,
        "show_names": show_names,
        "search_results": search_results,
        "episode_data": episode_data,
    }
