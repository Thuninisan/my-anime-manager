"""Torrent file parsing and parallel TMDB + Bangumi search.

Independent of batch_service.py — this is a standalone pipeline for
the ``POST /api/torrent/parse-and-search`` endpoint.

Flow:
  1. Bencode-extract file list from .torrent
  2. Parse each video file with anitopy (skip .ass / skip-dirs)
  3. Deduplicate show names (case-insensitive, frequency-ordered)
  4. Parallel TMDB + Bangumi search for each show name
  5. Organise into {default, backup} per source
"""

import asyncio
import re
from collections import Counter
from pathlib import Path

from ..utils.torrent_file_reader import read_torrent_file_list
from ..vendor.anitopy import parse as anitopy_parse
from ..clients import tmdb as tmdb_client
from ..clients import bangumi as bgm_client
from . import tmdb as tmdb_service
from . import bangumi as bangumi_service
from .. import data as data_store
from .. import config


# ═══════════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════════

SKIP_EXTENSIONS: set[str] = {
    ".ass", ".ssa", ".srt", ".idx", ".sub",   # subtitles
    ".7z", ".zip", ".rar", ".tar", ".gz",     # font archives
}

SKIP_DIR_PATTERNS: set[str] = {
    "cds", "scans", "sps", "specials",
    "extra", "extras", "bonus", "ost",
}

# anitopy anime_type values that indicate a non-episodic video file
# (creditless OP/ED, regular OP/ED, previews).  These files should be
# skipped even if anitopy assigns an episode_number to them (e.g. "ED1").
_NON_EPISODIC_TYPES: set[str] = {
    "CM",                             # commercial / 广告
    "ED", "ENDING", "NCED",          # ending / creditless ending
    "MENU",                           # DVD/BD menu
    "NCOP", "OP", "OPENING",         # opening / creditless opening
    "PREVIEW", "PV",                 # preview / promotional video
}


# ═══════════════════════════════════════════════════════════════════════
# Step 1: Bencode extraction (delegated to torrent_file_reader)
# ═══════════════════════════════════════════════════════════════════════
#
# Called inline in parse_and_search().


# ═══════════════════════════════════════════════════════════════════════
# Step 2: Per-file anitopy parsing
# ═══════════════════════════════════════════════════════════════════════

def _is_in_skip_directory(torrent_path: str) -> bool:
    """Check whether any directory component matches SKIP_DIR_PATTERNS.

    Args:
        torrent_path: Full path within the torrent (forward-slash separated).

    Returns:
        True if any directory segment (excluding the filename) is in the set.
    """
    parts = torrent_path.split("/")
    # Only inspect directory components — the last element is the filename.
    for part in parts[:-1]:
        if part.lower() in SKIP_DIR_PATTERNS:
            return True
    return False


def _extract_year(show_name: str) -> tuple[str, str | None]:
    """Extract a trailing 4-digit year from a show name.

    Examples:
        "Attack on Titan 2013" → ("Attack on Titan", "2013")
        "Show Name (2021)"     → ("Show Name", "2021")
        "Show Name"            → ("Show Name", None)

    Args:
        show_name: The anime_title from anitopy.

    Returns:
        (cleaned_name, year_or_None)
    """
    year_match = re.search(r"[\s\-–—]*(\d{4})$", show_name)
    if year_match:
        year = year_match.group(1)
        cleaned = re.sub(r"[\s\-–—]*\d{4}$", "", show_name).strip()
        return cleaned, year
    return show_name, None


def _parse_file(file_entry: dict) -> dict:
    """Parse a single torrent file entry with anitopy.

    Checks are applied in order:
      1. Skip by extension  (.ass, .ssa, …)
      2. Skip by directory  (CDs/, Scans/, SPs/, Extra/, …)
      3. anitopy.parse() on the bare filename
      4. Skip by non-episodic type  (NCED, OP, PV, …)

    Args:
        file_entry: A dict with ``"name"`` key (torrent-internal path).

    Returns:
        A dict describing the parse result (see module docstring for shape).
    """
    torrent_path: str = file_entry["name"]
    file_name: str = torrent_path.split("/")[-1] or torrent_path

    result: dict = {
        "file_name": file_name,
        "torrent_path": torrent_path,
        "show_name": None,
        "season": 1,
        "episode": 0,
        "is_extra": True,
        "skip_reason": None,
        "parsed": None,
    }

    # ── 1. Extension check ──
    ext = Path(file_name).suffix.lower()
    if ext in SKIP_EXTENSIONS:
        result["skip_reason"] = "skip_extension"
        return result

    # ── 2. Directory check ──
    if _is_in_skip_directory(torrent_path):
        result["skip_reason"] = "skip_directory"
        return result

    # ── 3. anitopy parse ──
    try:
        info = anitopy_parse(file_name)
    except Exception:
        result["skip_reason"] = "parse_failure"
        return result

    if not info:
        result["skip_reason"] = "parse_empty"
        return result

    anime_title: str = (info.get("anime_title") or "").strip()
    if not anime_title:
        result["skip_reason"] = "no_title"
        return result

    # ── 4. Non-episodic video type check (NCED, OP, PV, etc.) ──
    # anitopy returns anime_type as a string (single) or list (multiple).
    # Files like "ED1.mkv" would otherwise pass through with a spurious
    # episode_number matching.
    at_raw = info.get("anime_type")
    if at_raw:
        types = (
            [str(t).upper() for t in at_raw]
            if isinstance(at_raw, list)
            else [str(at_raw).upper()]
        )
        if any(t in _NON_EPISODIC_TYPES for t in types):
            result["skip_reason"] = "skip_non_episodic"
            return result

    # ── Success ──
    season_raw = info.get("anime_season")
    ep_raw = info.get("episode_number")

    season_num = int(season_raw) if season_raw else 1
    episode_num = int(ep_raw) if ep_raw else 0

    # Strip trailing year (e.g. "Show 2024" → "Show") so show_name
    # matches the search_results key produced by _search_*_for_name.
    base_name, _ = _extract_year(anime_title)
    # Append season suffix when > 1 so multi-season torrents produce
    # distinct show names (e.g. "Shield Hero" vs "Shield Hero Season 2").
    show_name = base_name
    if season_num > 1:
        show_name = f"{base_name} Season {season_num}"

    result["show_name"] = show_name
    result["season"] = season_num
    result["episode"] = episode_num
    result["parsed"] = dict(info)  # shallow copy — all values are strs/lists
    result["is_extra"] = False
    result["skip_reason"] = None
    return result


# ═══════════════════════════════════════════════════════════════════════
# Step 3: Show-name deduplication
# ═══════════════════════════════════════════════════════════════════════

def _deduplicate_show_names(parsed_files: list[dict]) -> list[str]:
    """Collect unique show names, ordered by frequency (descending).

    Case-insensitive dedup; preserves the casing of the most frequent
    variant for each distinct name.

    Args:
        parsed_files: List of successful parse results (is_extra=False).

    Returns:
        List of unique show names, most common first.
    """
    names = [p["show_name"] for p in parsed_files if p.get("show_name")]
    if not names:
        return []

    # Map case-insensitive key → (best_casing, count)
    freq: dict[str, tuple[str, int]] = {}
    for n in names:
        key = n.lower()
        if key in freq:
            existing, count = freq[key]
            freq[key] = (existing, count + 1)
        else:
            freq[key] = (n, 1)

    # Sort: highest count first; tie-break by original casing alphabetically
    sorted_items = sorted(freq.values(), key=lambda x: (-x[1], x[0]))
    return [name for name, _ in sorted_items]


# ═══════════════════════════════════════════════════════════════════════
# Step 4: Parallel TMDB + Bangumi search
# ═══════════════════════════════════════════════════════════════════════

async def _search_tmdb_for_name(show_name: str, search_as_movie: bool = False) -> dict:
    """Search TMDB for a single show name — return first + rest.

    Calls the TMDB client directly to get raw, unfiltered results.
    The cleaned name (with year stripped) is used as the searchby key
    in the final output.

    Args:
        show_name: Raw show name (year extracted internally).
        search_as_movie: If True, use /search/movie instead of /search/tv.

    Returns:
        dict with searchby, first (dict|None), rest (list[dict]), media_type.
    """
    cleaned_name, year = _extract_year(show_name)
    if search_as_movie:
        res = await tmdb_client.search_movie(cleaned_name, language="zh-CN")
    else:
        res = await tmdb_client.search_tv(cleaned_name, language="zh-CN")
    raw_results = res.json().get("results", [])
    first = raw_results[0] if raw_results else None
    # Build first_clean: TMDB /search/movie uses "title", /search/tv uses "name".
    # Also preserve original_title / original_name for frontend movie matching.
    if first:
        first_name = first.get("title") or first.get("name", "")
        first_clean = {"id": first["id"], "name": first_name}
        if search_as_movie:
            ot = first.get("original_title", "")
            if ot:
                first_clean["original_title"] = ot
        else:
            oname = first.get("original_name", "")
            if oname:
                first_clean["original_name"] = oname
    else:
        first_clean = None
    # Rest: id + name (title for movies) only, deduped by id
    seen_ids = {first["id"]} if first else set()
    rest = []
    for r in raw_results[1:]:
        rid = r.get("id")
        if rid and rid not in seen_ids:
            seen_ids.add(rid)
            rname = r.get("title") or r.get("name", "")
            entry = {"id": rid, "name": rname}
            if search_as_movie:
                ot = r.get("original_title", "")
                if ot:
                    entry["original_title"] = ot
            else:
                oname = r.get("original_name", "")
                if oname:
                    entry["original_name"] = oname
            rest.append(entry)
    return {
        "searchby": cleaned_name,
        "first": first_clean,
        "rest": rest,
        "media_type": "movie" if search_as_movie else "tv",
    }


def _alias_matches(subject: dict, target: str) -> bool:
    """Check if a subject's name / name_cn / infobox aliases match target.

    *target* is already lowercased and stripped.  Comparison is
    case-insensitive (effective for ASCII; no-op for CJK).

    Args:
        subject: A Bangumi search-result dict (may contain ``infobox``).
        target: Lowercased search keyword.

    Returns:
        True if any alias matches exactly.
    """
    # Check name and name_cn first (cheap, always available)
    name = (subject.get("name") or "").lower().strip()
    name_cn = (subject.get("name_cn") or "").lower().strip()
    if name == target or name_cn == target:
        return True

    # Check infobox aliases
    infobox = subject.get("infobox") or []
    for item in infobox:
        if item.get("key") == "别名":
            value = item.get("value")
            if isinstance(value, list):
                # value = [{"v": "当哒当"}, {"v": "DAN DA DAN"}, ...]
                for v in value:
                    alias = (
                        v.get("v") if isinstance(v, dict) else str(v)
                    ).lower().strip()
                    if alias == target:
                        return True
            elif isinstance(value, str):
                if value.lower().strip() == target:
                    return True
    return False


async def _search_bangumi_for_name(
    show_name: str, tmdb_id: int | None = None, tmdb_name: str | None = None,
) -> dict:
    """Search Bangumi for a single show name — return first + rest.

    For show names that do NOT contain "Season", alias-based matching
    is applied to pick the correct season-1 entry instead of blindly
    using the first result (which is often a later season).

    Fallback chain when the primary search yields nothing:
      1. tmdb_id → map.json reverse lookup
      2. tmdb_name (original Japanese title) → re-search Bangumi,
         taking the first result with NO map.json filter

    Args:
        show_name: Raw show name (year extracted internally).
        tmdb_id: Optional TMDB series ID for map fallback lookup.
        tmdb_name: Optional TMDB original name for re-search fallback.

    Returns:
        dict with searchby, first (dict|None), rest (list[dict]).
    """
    cleaned_name, _ = _extract_year(show_name)
    results = await bangumi_service.search_bangumi(cleaned_name)

    # ── Filter: keep only results whose Bangumi ID exists in map.json ──
    before = len(results)
    results = [r for r in results if data_store.get_bangumi_name(r["id"]) is not None]
    if len(results) < before:
        print(f"   🔍 Bangumi 过滤: {before} → {len(results)} (仅保留 map.json 中存在的 ID)")

    # ── Alias matching for non-Season show names ──
    # When "Season" is NOT in the show name, the first search result
    # is often Season 2/3 instead of Season 1.  Match against infobox
    # aliases to find the real Season 1 entry.
    if "season" not in cleaned_name.lower() and len(results) > 1:
        target = cleaned_name.lower().strip()
        best_idx = None
        for i, r in enumerate(results):
            if _alias_matches(r, target):
                best_idx = i
                break
        if best_idx is not None and best_idx > 0:
            matched = results.pop(best_idx)
            results.insert(0, matched)
            print(
                f"   🔀 别名匹配: "
                f"{matched.get('name_cn') or matched['name']} "
                f"[id: {matched['id']}] → 提升为首选"
            )

    # ── Fallback 1: tmdb_id → map.json reverse lookup ──
    if not results and tmdb_id is not None:
        bgm_id = data_store.get_bangumi_id_by_tmdb_id(tmdb_id)
        if bgm_id is not None:
            print(f"   🔄 Bangumi 搜索无结果，用 TMDB {tmdb_id} → map → Bangumi {bgm_id}")
            try:
                subject = await bgm_client.get_subject(bgm_id)
                name = subject.get("name", "")
                name_cn = subject.get("name_cn", "")
                eps = subject.get("eps") or subject.get("total_episodes") or 0
                return {
                    "searchby": cleaned_name,
                    "first": {
                        "id": bgm_id, "name": name, "eps": eps,
                        **({"name_cn": name_cn} if name_cn else {}),
                    },
                    "rest": [],
                }
            except Exception as exc:
                print(f"   ⚠️ Bangumi subject 获取失败 (id={bgm_id}): {exc}")

    # ── Fallback 2: re-search Bangumi with TMDB original name ──
    if not results and tmdb_name and tmdb_name.lower() != cleaned_name.lower():
        print(f'🔍 Bangumi 重搜 (TMDB 原名): "{tmdb_name}"')
        try:
            retry_results = await bangumi_service.search_bangumi(tmdb_name)
        except Exception:
            retry_results = []
        if retry_results:
            r = retry_results[0]
            print(
                f"   ✅ 重搜命中: "
                f"{r.get('name_cn') or r['name']} [id: {r['id']}]"
            )
            results = [r]  # take first result, no map.json filter

    first = results[0] if results else None
    # Pick only id + name + name_cn + eps for first entry
    first_clean = None
    if first:
        first_clean = {
            "id": first["id"],
            "name": first.get("name", ""),
            "eps": first.get("eps", 0),
        }
        if first.get("name_cn"):
            first_clean["name_cn"] = first["name_cn"]
    # Rest: id + name + name_cn, deduped by id
    seen_ids = {first["id"]} if first else set()
    rest = []
    for r in results[1:]:
        rid = r.get("id")
        if rid and rid not in seen_ids:
            seen_ids.add(rid)
            entry = {
                "id": rid,
                "name": r.get("name", ""),
                "eps": r.get("eps", 0),
            }
            if r.get("name_cn"):
                entry["name_cn"] = r["name_cn"]
            rest.append(entry)
    return {
        "searchby": cleaned_name,
        "first": first_clean,
        "rest": rest,
    }


async def _parallel_search(show_names: list[str], parsed_files: list[dict] | None = None) -> list[dict]:
    """Search TMDB + Bangumi for every show name concurrently.

    Within each show name, TMDB and Bangumi run in parallel.
    Across show names, Bangumi calls are serialised via a semaphore
    to respect the Bangumi client's built-in rate limiting.

    When a show name has ≤ 2 parsed files, TMDB uses /search/movie
    instead of /search/tv (movies typically have 1–2 files while TV
    series have many more).

    Args:
        show_names: Unique show names (frequency-ordered).
        parsed_files: Parsed file dicts (for per-show file counts).

    Returns:
        List of per-name search dicts, one per show name in order.
    """
    # Count files per show name to decide TV vs movie search
    file_counts: dict[str, int] = {}
    if parsed_files:
        for pf in parsed_files:
            sn = pf.get("show_name", "")
            file_counts[sn] = file_counts.get(sn, 0) + 1

    # Semaphore ensures only one Bangumi request is in-flight at a time.
    # Bangumi's internal _delay() sleeps before each HTTP call; without the
    # semaphore all concurrent calls would sleep in parallel then fire
    # simultaneously.
    bangumi_sem = asyncio.Semaphore(1)

    async def _search_pair(name: str) -> dict:
        """TMDB-first, then Bangumi.  Movies use TMDB original_title → Bangumi."""
        # Step 1: TMDB search (movie if ≤ 2 files, otherwise TV)
        count = file_counts.get(name, 0)
        search_as_movie = count <= 2
        tmdb_result = await _search_tmdb_for_name(name, search_as_movie=search_as_movie)

        # Step 2: Bangumi search
        if search_as_movie and tmdb_result["first"]:
            # Movie: search Bangumi with TMDB original_title, exclude TV platform
            original_title = tmdb_result["first"].get("original_title", "")
            if original_title:
                async with bangumi_sem:
                    bgm_raw = await bangumi_service.search_bangumi(original_title)
                # Filter: exclude TV platform entries only
                bgm_raw = [
                    r for r in bgm_raw
                    if r.get("platform", "") != "TV"
                ]
                first = bgm_raw[0] if bgm_raw else None
                if first:
                    bangumi_result: dict = {
                        "searchby": original_title,
                        "first": {
                            "id": first["id"],
                            "name": first.get("name", ""),
                            "eps": first.get("eps", 0),
                        },
                        "rest": [],
                    }
                    if first.get("name_cn"):
                        bangumi_result["first"]["name_cn"] = first["name_cn"]
                else:
                    bangumi_result = {"searchby": original_title, "first": None, "rest": []}
            else:
                bangumi_result = {"searchby": name, "first": None, "rest": []}
        else:
            # TV: existing fallback chain (primary name → map → tmdb_name)
            async def _do_bangumi():
                tmdb_first = tmdb_result["first"]
                tmdb_id = tmdb_first["id"] if tmdb_first else None
                tmdb_name = tmdb_first["name"] if tmdb_first else None
                async with bangumi_sem:
                    return await _search_bangumi_for_name(
                        name, tmdb_id=tmdb_id, tmdb_name=tmdb_name,
                    )

            bangumi_result = await _do_bangumi()

        return {
            "show_name": name,
            "tmdb": tmdb_result,
            "bangumi": bangumi_result,
        }

    # Launch all pairs concurrently — Bangumi serialisation is handled
    # inside each pair by the semaphore.
    raw = await asyncio.gather(
        *[_search_pair(name) for name in show_names],
        return_exceptions=True,
    )

    # Separate successes from failures
    pairs: list[dict] = []
    for i, result in enumerate(raw):
        if isinstance(result, BaseException):
            print(f"   ⚠️ 搜索 '{show_names[i]}' 异常: {result}")
            # Synthesise a failed pair so downstream logic stays simple
            name = show_names[i]
            cleaned, _ = _extract_year(name)
            pairs.append({
                "show_name": name,
                "tmdb": {"searchby": cleaned, "first": None, "rest": [], "media_type": "tv"},
                "bangumi": {"searchby": cleaned, "first": None, "rest": []},
            })
        else:
            pairs.append(result)

    return pairs


# ═══════════════════════════════════════════════════════════════════════
# Step 5: Organise into {default, backup}
# ═══════════════════════════════════════════════════════════════════════

def _organize(pairs: list[dict]) -> dict:
    """Build search_results (keyed by search term) and flattened backup.

    ``search_results``: key = cleaned search term → {tmdb, bangumi} first.
    ``search_results_backup``: flat ``{tmdb: [...], bangumi: [...]}``,
    merged across all search terms and deduplicated by id.

    Args:
        pairs: Search pair list from _parallel_search.

    Returns:
        ``{search_results: {…}, search_results_backup: {tmdb: […], bangumi: […]}}``
    """
    search_results: dict = {}

    # Flat backup: merge rest from all sources, dedup by id
    tmdb_backup: list[dict] = []
    tmdb_seen: set[int] = set()
    bangumi_backup: list[dict] = []
    bangumi_seen: set[int] = set()

    for p in pairs:
        tmdb_src = p["tmdb"]
        bgm_src = p["bangumi"]
        key = tmdb_src["searchby"]

        search_results[key] = {
            "tmdb": tmdb_src["first"],
            "bangumi": bgm_src["first"],
            "media_type": tmdb_src.get("media_type", "tv"),
        }

        # Collect rest, dedup by id
        for entry in tmdb_src["rest"]:
            rid = entry["id"]
            if rid not in tmdb_seen:
                tmdb_seen.add(rid)
                tmdb_backup.append(entry)
        for entry in bgm_src["rest"]:
            rid = entry["id"]
            if rid not in bangumi_seen:
                bangumi_seen.add(rid)
                bangumi_backup.append(entry)

    return {
        "search_results": search_results,
        "search_results_backup": {
            "tmdb": tmdb_backup,
            "bangumi": bangumi_backup,
        },
    }


# ═══════════════════════════════════════════════════════════════════════
# Step 5.5: Fetch episode listings for all discovered IDs
# ═══════════════════════════════════════════════════════════════════════

async def _fetch_episode_data(search_results: dict, parsed_files: list[dict]) -> dict:
    """Fetch TMDB season→episode maps and Bangumi episode lists.

    Collects every unique TMDB / Bangumi ID from *search_results*
    (first results only, NOT backup).

    If a Bangumi entry's ``eps`` is less than the number of parsed files
    for that show name, the sequel's episodes are fetched and stored under
    the sequel's own Bangumi ID.

    Args:
        search_results: Keyed by search term.
        parsed_files: List of parsed file dicts (for per-show file counts).

    Returns:
        ``{tmdb: {id: {season: …}}, bangumi: {id: {name, episodes}}}``
    """
    # ── Per-show file counts (exclude OVA/OAD + S0 specials — they are
    #     not regular TV episodes and should not trigger sequel expansion) ──
    _OVA_OAD_TYPES: set[str] = {"OVA", "OAD", "OAV"}
    file_counts: dict[str, int] = {}
    ova_show_names: set[str] = set()  # show_names that have OVA/OAD files
    for pf in parsed_files:
        sn = pf.get("show_name", "")
        # Season 0 = specials, not regular episodes
        if pf.get("season") == 0:
            continue
        # anitopy stores anime_type in the parsed dict; it may be a string
        # ("OVA") or a list (["OVA"]) when multiple types are detected.
        parsed = pf.get("parsed") or {}
        at = parsed.get("anime_type", "")
        if isinstance(at, list):
            at = at[0] if at else ""
        if at and str(at).upper() in _OVA_OAD_TYPES:
            ova_show_names.add(sn)  # track for 番外篇 expansion below
            continue  # OVA/OAD files don't count toward regular TV episode totals
        file_counts[sn] = file_counts.get(sn, 0) + 1

    # ── Collect unique IDs from search_results only ──
    tmdb_ids: set[int] = set()
    # Track media_type per TMDB ID so we know which ones are movies
    tmdb_media_types: dict[int, str] = {}
    bangumi_ids: set[int] = set()
    # Track which bangumi IDs need sequel expansion: bangumi_id → set of show_names
    sequel_map: dict[int, list[str]] = {}

    for key, entry in search_results.items():
        t = entry.get("tmdb")
        b = entry.get("bangumi")
        mt = entry.get("media_type", "tv")
        if t and t.get("id"):
            tmdb_ids.add(t["id"])
            tmdb_media_types[t["id"]] = mt
        if b and b.get("id"):
            bid = b["id"]
            bangumi_ids.add(bid)
            eps = b.get("eps", 0)
            fc = file_counts.get(key, 0)
            if eps > 0 and fc > eps:
                sequel_map.setdefault(bid, []).append(key)

    # ── Fetch TMDB season maps (TV) or movie pseudo-seasons ──
    tmdb_data: dict = {}
    for tid in sorted(tmdb_ids):
        try:
            mt = tmdb_media_types.get(tid, "tv")
            if mt == "movie":
                # Movies don't have seasons/episodes — the frontend
                # matches via direct name comparison instead.
                print(f"   TMDB movie {tid}: 跳过章节获取（前端名称匹配）")
                continue
            else:
                season_map = await tmdb_service.build_season_episode_map(tid)
                # TMDB now uses language=ja as the base, so episode names are
                # already Japanese originals — no second fetch needed.
                # Trim to the fields needed for the API response.
                output_seasons: dict = {}
                for s_num, s_data in season_map.items():
                    clean_eps = []
                    for ep in s_data["episodes"]:
                        clean_eps.append({
                            "epNum": ep["epNum"],
                            "tmdbId": ep["tmdbId"],
                            "name": ep["name"],
                        })
                    output_seasons[str(s_num)] = {
                        "name": s_data["name"],
                        "episodes": clean_eps,
                    }
                tmdb_data[str(tid)] = output_seasons
                total_eps = sum(len(v["episodes"]) for v in season_map.values())
                print(f"   TMDB {tid}: {len(season_map)} 季, {total_eps} 集")
        except Exception as exc:
            print(f"   ⚠️ TMDB {tid} 剧集获取失败: {exc}")
            tmdb_data[str(tid)] = {}

    # ── Fetch Bangumi episode lists (serial via semaphore) ──
    bangumi_data: dict = {}
    bgm_sem = asyncio.Semaphore(1)

    async def _fetch_one_bgm(bid: int):
        async with bgm_sem:
            # Get subject name
            try:
                subject = await bgm_client.get_subject(bid)
                name = subject.get("name_cn") or subject.get("name", str(bid))
            except Exception:
                name = str(bid)

            # Get all episodes in one request (no type filter), then
            # keep only main story (type=0) and SP (type=1) on our side.
            try:
                raw_eps = await bgm_client.get_episodes(bid, ep_type=None)
                eps = [
                    e for e in raw_eps
                    if e.get("type") in (0, 1)
                ]
            except Exception as exc:
                print(f"   ⚠️ Bangumi {bid} 剧集获取失败: {exc}")
                eps = []

        # Pick only sort + id + name + name_cn for each episode
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

        return str(bid), {"name": name, "episodes": clean_eps}

    tasks = [_fetch_one_bgm(bid) for bid in sorted(bangumi_ids)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for r in results:
        if isinstance(r, BaseException):
            print(f"   ⚠️ Bangumi fetch 异常: {r}")
        else:
            bid_str, data = r
            bangumi_data[bid_str] = data
            print(f"   Bangumi {bid_str} ({data['name']}): {len(data['episodes'])} 集")

    # ── Sequel expansion: if parsed file count > eps, fetch sequel episodes ──
    for primary_bid, show_keys in sequel_map.items():
        # Find sequel via relations
        sequel_bid: int | None = None
        try:
            async with bgm_sem:
                relations = await bgm_client.get_relations(primary_bid)
            for rel in relations:
                if rel.get("relation") == "续集":
                    sequel_bid = rel.get("id")
                    break
        except Exception as exc:
            print(f"   ⚠️ Bangumi {primary_bid} 关系获取失败: {exc}")

        if not sequel_bid:
            print(f"   ⚠️ Bangumi {primary_bid} 没有续集条目，但文件数超出 eps")
            continue
        if str(sequel_bid) in bangumi_data:
            continue  # already fetched

        print(f"   ↳ Bangumi {primary_bid} 文件数超出 eps，获取续集 {sequel_bid}")
        try:
            bid_str, data = await _fetch_one_bgm(sequel_bid)
            bangumi_data[bid_str] = data
            print(f"   Bangumi {bid_str} ({data['name']}): {len(data['episodes'])} 集")
        except Exception as exc:
            print(f"   ⚠️ 续集 {sequel_bid} 剧集获取失败: {exc}")

    # ── OVA/OAD special expansion: fetch 番外篇 episodes ──
    # When torrent contains OVA/OAD files, automatically pull episode data
    # from the primary Bangumi entry's related 番外篇 (side-story) subjects
    # so the frontend can match OVA/OAD files against them.
    if ova_show_names:
        # Map primary Bangumi ID → show_names that have OVA/OAD files
        ova_bgm_map: dict[int, set[str]] = {}
        for key, entry in search_results.items():
            if key in ova_show_names:
                b = entry.get("bangumi")
                if b and b.get("id"):
                    ova_bgm_map.setdefault(b["id"], set()).add(key)

        for primary_bid, show_keys in ova_bgm_map.items():
            # Find 番外篇 via relations
            special_bids: list[int] = []
            try:
                async with bgm_sem:
                    relations = await bgm_client.get_relations(primary_bid)
                for rel in relations:
                    if rel.get("relation") == "番外篇":
                        sid = rel.get("id")
                        if sid:
                            special_bids.append(sid)
            except Exception as exc:
                print(f"   ⚠️ Bangumi {primary_bid} 关系获取失败 (番外篇): {exc}")

            if not special_bids:
                print(f"   ⚠️ Bangumi {primary_bid} 有OVA/OAD文件但没有番外篇关联条目")
                continue

            for special_bid in special_bids:
                if str(special_bid) in bangumi_data:
                    continue  # already fetched

                print(f"   ↳ Bangumi {primary_bid} 有OVA/OAD文件，获取番外篇 {special_bid}")
                try:
                    bid_str, data = await _fetch_one_bgm(special_bid)
                    bangumi_data[bid_str] = data
                    print(f"   Bangumi {bid_str} ({data['name']}): {len(data['episodes'])} 集")
                except Exception as exc:
                    print(f"   ⚠️ 番外篇 {special_bid} 剧集获取失败: {exc}")

    return {
        "tmdb": tmdb_data,
        "bangumi": bangumi_data,
    }


# ═══════════════════════════════════════════════════════════════════════
# Top-level entry point
# ═══════════════════════════════════════════════════════════════════════

async def parse_and_search(torrent_path: str) -> dict:
    """Full pipeline: extract → parse → dedup → search → organise.

    This is the single entry point called by the API layer.

    Args:
        torrent_path: Filesystem path to a .torrent file.

    Returns:
        Nested dict with parsed_files, skipped_files, show_names,
        and search_results.  See module docstring for the full shape.

    Raises:
        RuntimeError: If no files can be parsed from the torrent.
    """
    torrent_name: str = Path(torrent_path).stem

    # ── Step 1: Bencode extraction ──
    print("📋 读取种子文件内容 (bencode)...")
    file_list: list[dict] = read_torrent_file_list(torrent_path)
    print(f"   → {len(file_list)} 个文件")

    # ── Collect subtitle files (before anitopy parsing skips them) ──
    subtitle_files: list[str] = [
        Path(f["name"]).name
        for f in file_list
        if Path(f["name"]).suffix.lower() in SKIP_EXTENSIONS
    ]
    if subtitle_files:
        print(f"   📝 {len(subtitle_files)} 个字幕文件")

    # ── Exclude-pattern filtering (before anitopy parsing) ──
    # Uses word-boundary matching so short keywords like "iv" don't
    # accidentally match inside words like "Live" or "Archive".
    raw_patterns: list[str] = [
        p.strip().lower()
        for p in config.TORRENT_EXCLUDE_PATTERNS.split(",")
        if p.strip()
    ]
    if raw_patterns:
        before = len(file_list)
        file_list = [
            f for f in file_list
            if not any(
                re.search(rf"(?:^|[^a-zA-Z]){re.escape(p)}(?:$|[^a-zA-Z])", f["name"].lower())
                for p in raw_patterns
            )
        ]
        excluded = before - len(file_list)
        if excluded:
            print(f"   🚫 排除关键词过滤: {excluded} 个文件被排除 ({', '.join(raw_patterns)})")

    # ── Filter out subtitle / font-archive / audio-only files ──
    # Subtitle files were already collected above; font archives and .mka
    # have no video content and don't need anitopy parsing.
    before_ext = len(file_list)
    file_list = [
        f for f in file_list
        if Path(f["name"]).suffix.lower() not in SKIP_EXTENSIONS
    ]
    ext_skipped = before_ext - len(file_list)
    if ext_skipped:
        print(f"   📎 非视频文件过滤: {ext_skipped} 个文件 (字幕/字体/音频)")
    print()

    # ── Step 2: Per-file anitopy parsing ──
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
    # Print skip-reason breakdown
    reason_counts = Counter(s["skip_reason"] for s in skipped_files)
    for reason, count in reason_counts.most_common():
        print(f"     - {reason}: {count}")
    print()

    if not parsed_files:
        raise RuntimeError("没有找到可处理的剧集文件")

    # ── Step 3: Deduplicate show names ──
    show_names: list[str] = _deduplicate_show_names(parsed_files)
    print(f"📛 去重节目名: {len(show_names)} 个")
    for i, name in enumerate(show_names):
        count = sum(1 for p in parsed_files if p.get("show_name", "").lower() == name.lower())
        print(f"   [{i + 1}] {name} ({count} 个文件)")
    print()

    # ── Step 4: Parallel TMDB + Bangumi search ──
    print("🔍 并行搜索 TMDB + Bangumi...")
    pairs: list[dict] = await _parallel_search(show_names, parsed_files=parsed_files)

    # Log summary
    for p in pairs:
        t = p["tmdb"]
        b = p["bangumi"]
        t_status = f"TMDB {'movie' if t.get('media_type') == 'movie' else ''} id={t['first']['id']}" if t["first"] else "TMDB 无结果"
        b_status = f"Bangumi id={b['first']['id']}" if b["first"] else "Bangumi 无结果"
        t_rest = f" +{len(t['rest'])} backup" if t["rest"] else ""
        b_rest = f" +{len(b['rest'])} backup" if b["rest"] else ""
        print(f"   [{p['show_name']}] {t_status}{t_rest}  |  {b_status}{b_rest}")
    print()

    # ── Step 5: Organise results ──
    organized = _organize(pairs)
    search_results = organized["search_results"]
    search_results_backup = organized["search_results_backup"]

    # Summary
    for key, entry in search_results.items():
        t = entry["tmdb"]
        b = entry["bangumi"]
        t_mt = entry.get("media_type", "tv")
        t_info = f"TMDB {'movie' if t_mt == 'movie' else ''} id={t['id']} ({t['name']})" if t else "TMDB 无结果"
        b_info = f"Bangumi id={b['id']} ({b.get('name_cn') or b['name']})" if b else "Bangumi 无结果"
        print(f"   [{key}] {t_info}")
        print(f"            {b_info}")
    print()

    # ── Step 5.5: Fetch episode listings ──
    print("📡 获取剧集数据...")
    episode_data = await _fetch_episode_data(search_results, parsed_files)
    print()

    # ── Step 6: Collect SP/Extra files (no re-parsing) ──
    # Files in special directories were marked is_extra during Step 2.
    # Return them directly so the frontend can present them for manual mapping.
    print("📦 收集 SP/Extra 目录文件...")
    specials: list[dict] = [
        {
            "file_name": r["file_name"],
            "torrent_path": r["torrent_path"],
        }
        for r in parsed_results if r["is_extra"]
    ]
    print(f"   → {len(specials)} 个特殊文件")
    print()

    return {
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
        "search_results_backup": search_results_backup,
        "episode_data": episode_data,
    }
