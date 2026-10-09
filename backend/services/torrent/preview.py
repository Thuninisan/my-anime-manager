"""Torrent preview: parse files, discover candidates, load provider catalogs.

TMDB searches run concurrently. TV candidates discover mapping links before
missing-provider searches; movies search Bangumi directly. Catalog requests
are deduplicated and run concurrently across providers.
"""

from ...domain.episode_adapters import episode_catalog, parsed_episode_ref

import logging
import asyncio
import re
from collections import Counter
from pathlib import Path

from ...utils.torrent_file_reader import read_torrent_file_list
from ...vendor.anitopy import parse as anitopy_parse
from ...clients import tmdb as tmdb_client
from ...clients import bangumi as bgm_client
from ...clients import tvdb as tvdb_client
from .. import tmdb as tmdb_service
from .. import bangumi as bangumi_service
from ... import data as data_store
from ... import config

logger = logging.getLogger(__name__)


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
    show_name, _ = _extract_year(anime_title)

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

def _preview_provider_result(provider, results, title=None, *, year=None, media_type="tv", source=None):
    """Preview keeps ordinary uncertainty as canonical evidence."""
    from ...domain.resource_adapters import provider_candidates
    from ..resource_resolver import rank_resource_candidates
    candidates = rank_resource_candidates(provider_candidates(provider, results, media_type, source), title=title, year=year)
    selected = next((r for r in results if candidates and str(r.get("id")) == str(candidates[0]["provider_id"])), None)
    return selected, {"candidates": candidates, "status": "suggested" if candidates else "unresolved"}


async def _search_tmdb_for_name(show_name: str, search_as_movie: bool = False) -> dict:
    """Rank TMDB candidates for one show and project selected + alternatives.

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

    first, recommendation = _preview_provider_result("tmdb", raw_results, cleaned_name, year=year,
                                   media_type="movie" if search_as_movie else "tv")
    # Build first_clean: TMDB /search/movie uses "title", /search/tv uses "name".
    # Also preserve original_title / original_name for frontend movie matching.
    if first:
        first_name = first.get("title") or first.get("name", "")
        logger.debug("TMDB search selected: %s", first_name)
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
    for r in raw_results:
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
        "recommendation": recommendation,
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
    *, media_type: str = "tv",
) -> dict:
    """Rank Bangumi candidates or a mapped candidate discovery for one show.

    For show names that do NOT contain "Season", alias-based matching
    is applied to pick the correct season-1 entry instead of blindly
    using the first result (which is often a later season).

    Fallback chain when the primary search yields nothing:
      1. tmdb_id → map.json reverse lookup
      2. tmdb_name (original Japanese title) → re-search Bangumi,
         ranking by title and year

    Args:
        show_name: Raw show name (year extracted internally).
        tmdb_id: Optional TMDB series ID for map fallback lookup.
        tmdb_name: Optional TMDB original name for re-search fallback.

    Returns:
        dict with searchby, first (dict|None), rest (list[dict]).
    """
    cleaned_name, year = _extract_year(show_name)
    # Mapping-table links discover related candidates before title search.
    # Ranking recommends a directory without confirming cross-platform identity.
    if tmdb_id is not None:

        linked = list({e["bangumi_id"]: dict(e, id=e["bangumi_id"])
                       for e in data_store.get_map_entries_by_tmdb_id(tmdb_id)
                       if (e.get("tmdb_season") == -1 if media_type == "movie" else e.get("tmdb_season") != -1)}.values())
        if linked:
            selected, recommendation = _preview_provider_result(
                "bangumi", linked, cleaned_name, year=year, media_type=media_type, source="existing_mapping")
            if recommendation["status"] != "unresolved":
                def project(row):
                    return {"id": row["id"], "name": row.get("name_original") or row.get("name", ""),
                            "name_cn": row.get("name", ""), "eps": 0}
                return {"searchby": cleaned_name, "recommendation": recommendation,
                        "first": project(selected) if selected else None,
                        "rest": [project(r) for r in linked if r is not selected]}
    results = await bangumi_service.search_bangumi(cleaned_name)

    # ── Alias matching for non-Season show names ──
    # When "Season" is NOT in the show name, the first search result
    # is often Season 2/3 instead of Season 1.  Match against infobox
    # aliases to find the real Season 1 entry.
    if "season" not in cleaned_name.lower() and len(results) > 1:
        alias_matches = [r for r in results if _alias_matches(r, cleaned_name.lower().strip())]
        if alias_matches:
            results = alias_matches + [r for r in results if r not in alias_matches]

    # ── Fallback 2: re-search Bangumi with TMDB original name ──
    if not results and tmdb_name and tmdb_name.lower() != cleaned_name.lower():
        logger.debug(f'🔍 Bangumi 重搜 (TMDB 原名): "{tmdb_name}"')
        retry_results = await bangumi_service.search_bangumi(tmdb_name)
        if retry_results:
            results = retry_results


    if media_type == "movie":
        results = [r for r in results if r.get("platform") != "TV"]
    first, recommendation = _preview_provider_result("bangumi", results, cleaned_name, year=year, media_type=media_type)
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
    for r in results:
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
        "recommendation": recommendation,
        "first": first_clean,
        "rest": rest,
    }


async def _gather_provider_requests(*requests):
    """Cancel unfinished sibling requests when a provider fails or preview is cancelled."""
    tasks = [asyncio.create_task(request) for request in requests]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


def _discovered_source(provider: str, rows: list[dict], keyword: str, media_type: str, *, source=None) -> dict:
    """Keep discovery order; search limits apply before any recommendation ranking."""
    from ...domain.resource_adapters import provider_candidates
    candidates = provider_candidates(provider, rows, media_type, source)
    projected = [{"id": c["provider_id"], "name": c["title"] or c["original_title"] or ""}
                 for c in candidates]
    return {"searchby": keyword, "first": projected[0] if projected else None,
            "rest": projected[1:], "recommendation": {
                "candidates": candidates, "status": "suggested" if candidates else "unresolved"}}


async def _parallel_search(show_names: list[str], parsed_files: list[dict] | None = None) -> list[dict]:
    """TMDB discovery, TV mapping, then parallel searches for missing sources."""
    counts = Counter(pf.get("show_name", "").lower() for pf in parsed_files or [])
    tmdb_results = await asyncio.gather(*[
        _search_tmdb_for_name(name, search_as_movie=counts[name.lower()] <= 2)
        for name in show_names], return_exceptions=True)
    for result in tmdb_results:
        if isinstance(result, (asyncio.CancelledError, ValueError)):
            raise result
        if isinstance(result, BaseException):
            raise RuntimeError("resource_provider_request_failed") from result

    async def discover(name, tmdb):
        media_type = tmdb["media_type"]
        first = tmdb["first"] or {}
        links = []
        if media_type == "tv" and first.get("id"):
            links = [dict(row) for row in data_store.get_map_entries_by_tmdb_id(first["id"])
                     if row.get("tmdb_season") != -1]
        bgm_rows = list({row["bangumi_id"]: dict(row, id=row["bangumi_id"],
                        name_cn=row.get("name", ""), name=row.get("name_original") or row.get("name", ""))
                        for row in links if row.get("bangumi_id")}.values())
        tvdb_rows = list({row["tvdb_id"]: row for row in links if row.get("tvdb_id")}.values())
        keyword = first.get("name") or first.get("original_title") or first.get("original_name") or name

        async def bangumi():
            if bgm_rows:
                return _discovered_source("bangumi", bgm_rows, keyword, media_type, source="existing_mapping")
            rows = await bangumi_service.search_bangumi(keyword)
            if media_type == "movie":
                rows = [row for row in rows if row.get("platform") != "TV"]
            rows = list({row["id"]: row for row in rows if row.get("id")}.values())
            return _discovered_source("bangumi", rows[:2 if media_type == "movie" else 5], keyword, media_type)

        async def tvdb():
            if media_type == "movie":
                return _discovered_source("tvdb", [], name, media_type)
            if tvdb_rows:
                return _discovered_source("tvdb", tvdb_rows, name, media_type, source="existing_mapping")
            response = await tvdb_client.search_series(name)
            rows = [dict(row, id=row.get("tvdb_id") or row.get("id"))
                    for row in response.json().get("data", [])
                    if row.get("type", "series") == "series"]
            rows = [row for row in rows if str(row.get("id", "")).isdigit() and int(row["id"]) > 0]
            return _discovered_source("tvdb", rows[:1], name, media_type)

        bgm, tv = await _gather_provider_requests(bangumi(), tvdb())
        return {"show_name": name, "tmdb": tmdb, "bangumi": bgm, "tvdb": tv, "map_entries": links}

    results = await asyncio.gather(*[discover(name, result) for name, result in zip(show_names, tmdb_results)],
                                   return_exceptions=True)
    for result in results:
        if isinstance(result, (asyncio.CancelledError, ValueError)):
            raise result
        if isinstance(result, BaseException):
            raise RuntimeError("resource_provider_request_failed") from result
    return results


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
            "provider_recommendations": {"tmdb": tmdb_src["recommendation"], "bangumi": bgm_src["recommendation"],
                **({"tvdb": p["tvdb"]["recommendation"]} if "tvdb" in p else {})},
            "map_entries": p.get("map_entries", []),
            "discovery_complete": True,
            "bangumi_ids": [c["provider_id"] for c in bgm_src["recommendation"]["candidates"]],
            "tvdb_ids": [c["provider_id"] for c in p.get("tvdb", {}).get("recommendation", {}).get("candidates", [])],
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

async def _fetch_provider_catalogs(search_results: dict, parsed_files: list[dict]) -> dict:
    """Load discovered directories concurrently, without expanding relation chains.

    Legacy callers can still supply TMDB IDs and mapping hints directly.
    Movie discovery never queries mapping or loads TVDB/ TMDB seasons.
    """
    tmdb_ids, bangumi_ids, tvdb_ids = set(), set(), set()
    for entry in search_results.values():
        movie = entry.get("media_type") == "movie"
        t, b = entry.get("tmdb") or {}, entry.get("bangumi") or {}
        if t.get("id") and not movie:
            tmdb_ids.add(t["id"])
        # Legacy callers may still supply only TMDB and mapping hints.
        if not movie and not entry.get("discovery_complete") and t.get("id"):
            links = [row for row in data_store.get_map_entries_by_tmdb_id(t["id"])
                     if row.get("tmdb_season") != -1]
            entry["map_entries"] = links or entry.get("map_entries", [])
        if b.get("id"):
            bangumi_ids.add(b["id"])
        bangumi_ids.update(entry.get("bangumi_ids", []))
        if not movie:
            tvdb_ids.update(entry.get("tvdb_ids", []))
            for row in entry.get("map_entries", []):
                if row.get("bangumi_id"):
                    bangumi_ids.add(row["bangumi_id"])
                if row.get("tvdb_id"):
                    tvdb_ids.add(row["tvdb_id"])
            entry["bangumi_ids"] = sorted(set(entry.get("bangumi_ids", [])) |
                {row["bangumi_id"] for row in entry.get("map_entries", []) if row.get("bangumi_id")})

    # ── Fetch TMDB season maps (TV) or movie pseudo-seasons ──
    tmdb_data: dict = {}
    tmdb_sem = asyncio.Semaphore(4)

    async def _fetch_one_tmdb(tid):
        try:
            async with tmdb_sem:
                season_map = await tmdb_service.build_season_episode_map(tid, strict=True)
                # TMDB now uses language=ja as the base, so episode names are
                # already Japanese originals — no second fetch needed.
                # Include all fields needed for downstream NFO generation.
                output_seasons: dict = {}
                for s_num, s_data in season_map.items():
                    clean_eps = []
                    for ep in s_data["episodes"]:
                        clean_eps.append({
                            "epNum": ep["epNum"],
                            "tmdbId": ep["tmdbId"],
                            "name": ep["name"],
                            "overview": ep.get("overview", ""),
                            "airDate": ep.get("airDate", ""),
                            "runtime": ep.get("runtime", 0),
                            "stillPath": ep.get("stillPath", ""),
                            "voteAverage": ep.get("voteAverage", 0),
                            "voteCount": ep.get("voteCount"),
                            "directors": ep.get("directors", []),
                            "writers": ep.get("writers", []),
                            "guestStars": ep.get("guestStars", []),
                        })
                    output_seasons[str(s_num)] = {
                        "name": s_data["name"],
                        "episodes": clean_eps,
                    }
                tmdb_data[str(tid)] = output_seasons
                total_eps = sum(len(v["episodes"]) for v in season_map.values())
                logger.debug(f"   TMDB {tid}: {len(season_map)} 季, {total_eps} 集")
        except Exception as exc:
            raise RuntimeError("preview_provider_fetch_failed: tmdb") from exc

    # ── Fetch Bangumi episode lists with bounded concurrency ──
    bangumi_data: dict = {}
    bgm_sem = asyncio.Semaphore(2)

    async def _fetch_one_bgm(bid: int):
        async with bgm_sem:
            # Get subject name
            try:
                subject = await bgm_client.get_subject(bid)
                name = subject.get("name_cn") or subject.get("name", str(bid))
            except Exception as exc:
                raise RuntimeError("preview_provider_fetch_failed: bangumi") from exc

            # Get all episodes in one request (no type filter), then
            # keep only main story (type=0) and SP (type=1) on our side.
            try:
                raw_eps = await bgm_client.get_episodes(bid, ep_type=None)
                eps = [
                    e for e in raw_eps
                    if e.get("type") in (0, 1)
                ]
            except Exception as exc:
                logger.warning(f"   ⚠️ Bangumi {bid} 剧集获取失败: {exc}")
                raise RuntimeError("preview_provider_fetch_failed: bangumi") from exc

        # Pick only sort + id + name + name_cn for each episode
        clean_eps = []
        for ep in eps:
            entry = {
                "sort": ep.get("sort") or ep.get("ep", 0),
                "ep": ep.get("ep"),
                "raw_sort": ep.get("sort"),
                "desc": ep.get("desc"),
                "airDate": ep.get("airdate"),
                    "id": ep["id"],
                "name": ep.get("name", ""),
            }
            cn = ep.get("name_cn")
            if cn and cn != entry["name"]:
                entry["name_cn"] = cn
            clean_eps.append(entry)
        clean_eps.sort(key=lambda x: x["sort"])

        return str(bid), {"name": name, "episodes": clean_eps}

    tvdb_data: dict = {}
    from ..tvdb import fetch_tvdb_series_episodes
    tvdb_sem = asyncio.Semaphore(2)

    async def _fetch_one_tvdb(tid):
        async with tvdb_sem:
            result = await fetch_tvdb_series_episodes(tid)
        if result is None:
            raise RuntimeError("preview_provider_fetch_failed: tvdb")
        tvdb_data[str(tid)] = result

    async def _store_bgm(bid):
        key, result = await _fetch_one_bgm(bid)
        bangumi_data[key] = result
        logger.debug("Bangumi %s (%s): %d episodes", key, result["name"], len(result["episodes"]))

    await _gather_provider_requests(
        *[_fetch_one_tmdb(tid) for tid in sorted(tmdb_ids)],
        *[_store_bgm(bid) for bid in sorted(bangumi_ids)],
        *[_fetch_one_tvdb(tid) for tid in sorted(tvdb_ids)],
    )

    return {
        "tmdb": tmdb_data,
        "bangumi": bangumi_data,
        "tvdb": tvdb_data,
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
        Unified parsed_files inventory, show_names, search_results and provider catalogs.

    Raises:
        RuntimeError: If no files can be parsed from the torrent.
    """
    # Read the real torrent name from the info dict, not the temp filename
    from ...utils.torrent_file_reader import read_torrent_name
    torrent_name = read_torrent_name(torrent_path)

    # ── Branch: ktnbytes / 343-Labs → TMDB-first flow ──
    if "ktnbytes" in torrent_name.lower() or "343-labs" in torrent_name.lower():
        logger.debug(f"🔀 检测到 ktnbytes/343-Labs 种子，使用 TMDB 直搜流程")
        from .search import search_by_tmdb
        return await search_by_tmdb(torrent_path, torrent_name=torrent_name)

    from .preview_files import exclude_paths, file_type, unify_files

    # ── Step 1: Bencode extraction ──
    logger.debug("📋 读取种子文件内容 (bencode)...")
    file_list: list[dict] = read_torrent_file_list(torrent_path)
    original_files = list(file_list)
    excluded_paths: set[str] = set()
    logger.debug(f"   → {len(file_list)} 个文件")

    # ── Collect subtitle files (before anitopy parsing skips them) ──
    subtitle_files: list[str] = [
        f["name"]
        for f in file_list
        if Path(f["name"]).suffix.lower() in {".ass", ".ssa", ".srt", ".sub", ".idx", ".vtt", ".ttml", ".sbv", ".dfxp"}
    ]
    if subtitle_files:
        logger.debug(f"   📝 {len(subtitle_files)} 个字幕文件")

    # ── Exclude-pattern filtering (before anitopy parsing) ──
    # Uses word-boundary matching so short keywords like "iv" don't
    # accidentally match inside words like "Live" or "Archive".
    excluded_paths = exclude_paths(original_files, config.TORRENT_EXCLUDE_PATTERNS)
    file_list = [file for file in file_list if file["name"] not in excluded_paths]
    if excluded_paths:
        logger.info("排除关键词过滤: %d 个文件被排除", len(excluded_paths))

    # ── Filter out subtitle / font-archive / audio-only files ──
    # Subtitle files were already collected above; font archives and .mka
    # have no video content and don't need anitopy parsing.
    before_ext = len(file_list)
    file_list = [
        f for f in file_list
        if file_type(f["name"]) == "video"
    ]
    ext_skipped = before_ext - len(file_list)
    if ext_skipped:
        logger.debug(f"   📎 非视频文件过滤: {ext_skipped} 个文件 (字幕/字体/音频)")

    # ── Step 2: Per-file anitopy parsing ──
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
    # Print skip-reason breakdown
    reason_counts = Counter(s["skip_reason"] for s in skipped_files)
    for reason, count in reason_counts.most_common():
        logger.debug(f"     - {reason}: {count}")

    if not parsed_files:
        raise RuntimeError("没有找到可处理的剧集文件")

    # ── Step 3: Deduplicate show names ──
    show_names: list[str] = _deduplicate_show_names(parsed_files)
    logger.debug(f"📛 去重节目名: {len(show_names)} 个")
    for i, name in enumerate(show_names):
        count = sum(1 for p in parsed_files if p.get("show_name", "").lower() == name.lower())
        logger.debug(f"   [{i + 1}] {name} ({count} 个文件)")

    # ── Step 4: Parallel TMDB + Bangumi search ──
    logger.debug("Parallel TMDB search, mapping discovery and missing-provider searches")
    pairs: list[dict] = await _parallel_search(show_names, parsed_files=parsed_files)

    # Log summary
    for p in pairs:
        t = p["tmdb"]
        b = p["bangumi"]
        t_status = f"TMDB {'movie' if t.get('media_type') == 'movie' else ''} id={t['first']['id']}" if t["first"] else "TMDB 无结果"
        b_status = f"Bangumi id={b['first']['id']}" if b["first"] else "Bangumi 无结果"
        t_rest = f" +{len(t['rest'])} backup" if t["rest"] else ""
        b_rest = f" +{len(b['rest'])} backup" if b["rest"] else ""
        logger.debug(f"   [{p['show_name']}] {t_status}{t_rest}  |  {b_status}{b_rest}")

    # ── Step 5: Organise results ──
    organized = _organize(pairs)
    search_results = organized["search_results"]
    search_results_backup = organized["search_results_backup"]

    # ── Fallback: TMDB not found but Bangumi found → look up map ──
    from ... import data as data_store

    for key, entry in search_results.items():
        if entry.get("media_type") != "movie" and entry["tmdb"] is None and entry["bangumi"] is not None:
            bgm_id = entry["bangumi"]["id"]
            mapped_tmdb_id = data_store.get_tmdb_id(bgm_id)
            if mapped_tmdb_id and entry.get("media_type") != "movie":
                entry["tmdb"] = {
                    "id": mapped_tmdb_id,
                    "name": f"TMDB {mapped_tmdb_id}",
                }
                logger.debug(f"   [{key}] TMDB 回退: Bangumi {bgm_id} → TMDB {mapped_tmdb_id}")

    # Summary
    for key, entry in search_results.items():
        t = entry["tmdb"]
        b = entry["bangumi"]
        t_mt = entry.get("media_type", "tv")
        t_info = f"TMDB {'movie' if t_mt == 'movie' else ''} id={t['id']} ({t['name']})" if t else "TMDB 无结果"
        b_info = f"Bangumi id={b['id']} ({b.get('name_cn') or b['name']})" if b else "Bangumi 无结果"
        logger.debug(f"   [{key}] {t_info}")
        logger.debug(f"            {b_info}")

    logger.info("Fetching TMDB, TVDB and Bangumi episode catalogs concurrently")
    provider_catalogs = await _fetch_provider_catalogs(search_results, parsed_files)

    # Default to an available index; explicit user switches remain strict.
    available_index = "tvdb" if provider_catalogs.get("tvdb") else "tmdb"

    # ── Step 6: Collect SP/Extra files (no re-parsing) ──
    # Files in special directories were marked is_extra during Step 2.
    # Return them directly so the frontend can present them for manual mapping.
    logger.debug("📦 收集 SP/Extra 目录文件...")
    specials: list[dict] = [
        {
            "file_name": r["file_name"],
            "torrent_path": r["torrent_path"],
            "show_name": r.get("show_name", ""),
            "parsed_episode": parsed_episode_ref(r),
        }
        for r in parsed_results if r["is_extra"]
    ]
    logger.debug(f"   → {len(specials)} 个特殊文件")

    from .preview_session import candidate_context
    search_results = {key: candidate_context(key, entry, provider_catalogs) for key, entry in search_results.items()}

    return unify_files({
        "index": available_index,
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
                "parsed_episode": parsed_episode_ref(p),
            }
            for p in parsed_files
        ],
        "specials": specials,
        "skipped_files": skipped_files,
        "show_names": show_names,
        "search_results": search_results,
        "search_results_backup": search_results_backup,
        "provider_catalogs": provider_catalogs,
        "episode_catalog": episode_catalog(provider_catalogs),
    }, original_files, excluded_paths)


# ═════════════════════════════════════════════════════════════════════
# Series name derivation
# ═════════════════════════════════════════════════════════════════════

def derive_series_name(snapshot: dict | None, files: list[dict] | None = None) -> str:
    """Use submitted selections for naming; distinct works retain their own roots."""
    if files is not None:
        selected = [f for f in files if not f.get("is_subtitle") and f.get("resource_identity")]
        works = {(f["resource_identity"]["media_type"],
                  f["resource_identity"]["tmdb_series_id"] or f["resource_identity"]["tmdb_movie_id"],
                  f["resource_identity"]["tvdb_series_id"] if not f["resource_identity"]["tmdb_series_id"] else None)
                 for f in selected}
        if len(works) > 1:
            return ""
        return next((f.get("tmdb_show_name") or f.get("bangumi_show_name") for f in selected
                     if f.get("tmdb_show_name") or f.get("bangumi_show_name")), "")
    return next((series["display_name"] for series in (snapshot or {}).get("series_contexts", {}).values()
                 if series["display_name"]), "")
