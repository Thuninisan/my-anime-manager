"""Alternative torrent search flow for ktnbytes / 343-Labs releases.

These releases have known-good titles parsed by anitopy — we search TMDB
directly, then pull episode data from TMDB + related Bangumi + TVDB
entries discovered through the mapping table.
"""

import asyncio
from collections import Counter
from pathlib import Path

from ..torrent_preview import _parse_file, _deduplicate_show_names, SKIP_EXTENSIONS
from ...utils.torrent_file_reader import read_torrent_file_list, read_torrent_name
from ...clients import tmdb as tmdb_client
from ...clients import bangumi as bgm_client
from .. import tmdb as tmdb_service
from ..tvdb import fetch_tvdb_series_episodes
from ... import data as data_store
from ... import config


# ═══════════════════════════════════════════════════════════════════════
# TMDB search helper
# ═══════════════════════════════════════════════════════════════════════

async def _search_tmdb_single(name: str) -> dict | None:
    """Search TMDB for a single show name, returning first TV result.

    Falls back to ``language="zh-CN"`` when the default (ja) yields
    no results.

    Args:
        name: Show name to search for.

    Returns:
        dict with ``id, name, original_name, first_air_date, overview``,
        or ``None`` if no result was found.
    """
    # Primary search with default language (ja)
    res = await tmdb_client.search_tv(name)
    results = res.json().get("results", [])

    # Fallback: re-search with zh-CN
    if not results:
        print(f'   🔄 TMDB 无结果 (ja)，用 zh-CN 重搜: "{name}"')
        res = await tmdb_client.search_tv(name, language="zh-CN")
        results = res.json().get("results", [])

    if not results:
        print(f'   ❌ TMDB 无结果: "{name}"')
        return None

    first = results[0]
    return {
        "id": first["id"],
        "name": first.get("name", ""),
        "original_name": first.get("original_name", ""),
        "first_air_date": first.get("first_air_date", ""),
        "overview": first.get("overview", ""),
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
        name = str(bgm_id)

    try:
        raw_eps = await bgm_client.get_episodes(bgm_id, ep_type=None)
        eps = [e for e in raw_eps if e.get("type") in (0, 1)]
    except Exception:
        print(f"   ⚠️ Bangumi {bgm_id} 剧集获取失败")
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
    if map_entries:
        print(f"   🗺 map.json: {len(map_entries)} 个关联条目 (TMDB {tmdb_id})")
        for me in map_entries:
            tvdb_str = f"tvdb={me['tvdb_id']}" if me.get("tvdb_id") else "tvdb=N/A"
            print(f"     - Bangumi {me['bangumi_id']} ({me['name']})  {tvdb_str}")

    # ── Collect unique Bangumi IDs ──
    bangumi_ids: list[int] = sorted({me["bangumi_id"] for me in map_entries})

    # ── Collect unique TVDB IDs ──
    tvdb_ids: list[int] = sorted({
        me["tvdb_id"] for me in map_entries
        if me.get("tvdb_id") is not None
    })

    # ── Fetch Bangumi episodes (serial via semaphore) ──
    bgm_sem = asyncio.Semaphore(1)
    bangumi_data: dict[str, dict] = {}

    async def _fetch_one_bgm(bid: int):
        async with bgm_sem:
            return str(bid), await _fetch_bangumi_episodes(bid)

    if bangumi_ids:
        bgm_tasks = [_fetch_one_bgm(bid) for bid in bangumi_ids]
        bgm_results = await asyncio.gather(*bgm_tasks, return_exceptions=True)
        for r in bgm_results:
            if isinstance(r, BaseException):
                print(f"   ⚠️ Bangumi fetch 异常: {r}")
            elif r[1] is not None:
                bid_str, data = r
                bangumi_data[bid_str] = data
                print(f"   Bangumi {bid_str} ({data['name']}): {len(data['episodes'])} 集")

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
        for r in tvdb_results:
            if isinstance(r, BaseException):
                print(f"   ⚠️ TVDB fetch 异常: {r}")
            elif r[1] is not None:
                tid_str, data = r
                tvdb_data[tid_str] = data

    # ── Await TMDB ──
    try:
        tmdb_season_map = await tmdb_task
    except Exception as exc:
        print(f"   ⚠️ TMDB {tmdb_id} season map 获取失败: {exc}")
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

    print("📋 读取种子文件内容 (bencode)...")
    file_list: list[dict] = read_torrent_file_list(torrent_path)
    print(f"   → {len(file_list)} 个文件")

    # ── Collect subtitle files before anitopy parsing ──
    subtitle_files: list[str] = [
        Path(f["name"]).name
        for f in file_list
        if Path(f["name"]).suffix.lower() in SKIP_EXTENSIONS
    ]
    if subtitle_files:
        print(f"   📝 {len(subtitle_files)} 个字幕文件")

    # ── Filter subtitle / font-archive files ──
    before_ext = len(file_list)
    file_list = [
        f for f in file_list
        if Path(f["name"]).suffix.lower() not in SKIP_EXTENSIONS
    ]
    ext_skipped = before_ext - len(file_list)
    if ext_skipped:
        print(f"   📎 非视频文件过滤: {ext_skipped} 个文件 (字幕/字体/音频)")
    print()

    # ── Step 2: anitopy parsing ──
    print("🔧 anitopy 逐文件解析...")
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

    print(f"   合规剧集: {len(parsed_files)} 个")
    print(f"   跳过文件: {len(skipped_files)} 个")
    reason_counts = Counter(s["skip_reason"] for s in skipped_files)
    for reason, count in reason_counts.most_common():
        print(f"     - {reason}: {count}")
    print()

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
    print(f"📛 去重节目名: {len(show_names)} 个")
    for i, name in enumerate(show_names):
        count = sum(
            1 for p in parsed_files
            if p.get("show_name", "").lower() == name.lower()
        )
        print(f"   [{i + 1}] {name} ({count} 个文件)")
    print()

    # ── Step 4 + 5 + 6: Search TMDB → map lookup → fetch episode data ──
    print("🔍 TMDB 直搜 + 关联数据获取...")

    search_results: dict = {}
    episode_data: dict = {
        "tmdb": {},
        "bangumi": {},
        "tvdb": {},
    }

    for name in show_names:
        print(f'\n   🔎 搜索: "{name}"')
        tmdb_info = await _search_tmdb_single(name)

        if tmdb_info is None:
            search_results[name] = {
                "tmdb": None,
                "bangumi_ids": [],
                "tvdb_ids": [],
                "map_entries": [],
            }
            continue

        tmdb_id = tmdb_info["id"]
        print(f"   ✅ TMDB {tmdb_id}: {tmdb_info['name']} ({tmdb_info.get('original_name', '')})")

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

    print()

    return {
        "index": "tmdb",
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
