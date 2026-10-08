"""TMDB business logic layer."""

import logging

from ..clients import tmdb as tmdb_client

logger = logging.getLogger(__name__)

# TMDB genre ID for Animation
GENRE_ANIMATION = 16


def filter_animation_candidates(results: list[dict], query: str) -> list[dict]:
    """Require Animation for automatic matching, preserving TMDB result order."""
    candidates = []
    for result in results:
        genres = result.get("genre_ids") or []
        if GENRE_ANIMATION in genres:
            candidates.append(result)
        else:
            logger.info(
                "tmdb.candidate_excluded query=%r id=%s title=%r genre_ids=%s "
                "reason=missing_animation_genre",
                query, result.get("id"), result.get("name") or result.get("title"), genres,
            )
    if not candidates:
        logger.warning(
            "tmdb.no_animation_match query=%r total=%d; manual selection required",
            query, len(results),
        )
    return candidates


def _format_show(show: dict) -> dict:
    """Extract needed fields from a TMDB search result."""
    return {
        "id": show["id"],
        "name": show["name"],
        "original_name": show.get("original_name"),
        "first_air_date": show.get("first_air_date"),
    }


def _print_candidates(label: str, items: list[dict]) -> None:
    """Print current candidate list for debugging."""
    names = [
        f"{s['name']} ({s.get('first_air_date', '?')[:4]})"
        for s in items[:4]
    ]
    logger.debug(f"   {label}: [{len(items)}] {', '.join(names)}")


async def search_tv_show(
    show_name: str, prefer_year: str | None = None
) -> dict | None:
    """Search TMDB for a TV show, returning the best match.

    Filter pipeline:
    1. Require Animation genre for every result, including a single result
    2. No animation results → return unmatched (no unfiltered fallback)
    3. Still multiple → if year known, filter by year
    4. Still multiple → exact title match (name / original_name)
    5. Still multiple → ambiguous_resource; never choose by popularity

    Args:
        show_name: Show name to search for
        prefer_year: Optional 4-digit year (extracted from torrent filename)

    Returns:
        dict with id, name, original_name, first_air_date or None
    """
    logger.debug(f'🔍 TMDB 搜索: "{show_name}"')
    if prefer_year:
        logger.debug(f"   偏好年份: {prefer_year}")

    res = await tmdb_client.search_tv(show_name)
    results = res.json().get("results", [])
    if not results:
        logger.info("TMDB 搜索无结果: %s", show_name)
        return None

    results = filter_animation_candidates(results, show_name)
    if not results:
        return None

    from .resource_resolver import select_provider_result
    # Fetch aliases only when search title/year cannot disambiguate locally.
    from ..domain.resource_adapters import provider_candidates
    from .resource_resolver import ResourceResolver
    initial = ResourceResolver().resolve(provider_candidates("tmdb", results), title=show_name, year=prefer_year)
    if initial["status"] == "ambiguous":
        for row in results:
            try:
                response = await tmdb_client.get_alternative_titles(row["id"])
                row["alternative_titles"] = response.json().get("results", [])
            except Exception:
                pass
    show = select_provider_result("tmdb", results, show_name, year=prefer_year)
    return _format_show(show) if show is not None else None


async def get_tv_show_detail(tv_id: int, language: str = "") -> dict:
    """Get detailed TV show information.

    Args:
        tv_id: TMDB show ID.
        language: Optional language override (e.g. ``"zh-CN"``).

    Returns:
        dict with full show details including studios, genres
    """
    logger.info("📡 获取 TMDB 详情...")
    res = await tmdb_client.get_tv_detail(tv_id, language=language)
    data = res.json()

    # Extract studios from networks, fallback to production companies
    studios = []
    for net in data.get("networks", []):
        studios.append(net["name"])
    if not studios:
        for comp in data.get("production_companies", []):
            studios.append(comp["name"])

    genres = [g["name"] for g in data.get("genres", [])]

    return {
        "id": data["id"],
        "name": data["name"],
        "original_name": data.get("original_name"),
        "first_air_date": data.get("first_air_date"),
        "overview": data.get("overview", ""),
        "number_of_seasons": data.get("number_of_seasons", 0),
        "number_of_episodes": data.get("number_of_episodes", 0),
        "episode_groups": data.get("episode_groups", {}).get("results", []),
        "status": data.get("status", ""),
        "vote_average": data.get("vote_average", 0),
        "poster_path": data.get("poster_path", ""),
        "backdrop_path": data.get("backdrop_path", ""),
        "studios": studios,
        "genres": genres,
    }


def find_best_episode_group(groups: list[dict]) -> dict | None:
    """Pick the best episode group from the list.

    Priority:
    1. Name matches "Seasons" or "All Seasons" (case insensitive)
    2. Highest group_count

    Args:
        groups: List of episode group dicts

    Returns:
        Best matching group dict or None
    """
    if not groups:
        return None

    # First priority: name matches "season" / "all" / "série"
    season_match = [
        g for g in groups
        if any(kw in g.get("name", "").lower() for kw in ("season", "all", "série", "serie"))
    ]
    if season_match:
        season_match.sort(key=lambda g: g.get("group_count", 0), reverse=True)
        return season_match[0]

    # Second priority: sort by group_count descending
    sorted_groups = sorted(groups, key=lambda g: g.get("group_count", 0), reverse=True)
    return sorted_groups[0]


async def build_season_episode_map(
    tv_id: int, language: str = "", *, tv_detail: dict | None = None, strict: bool = False,
    season_numbers: set[int] | None = None,
) -> dict[int, dict]:
    """Build a TMDB season→episodes mapping using the default Season API.

    Does NOT compute cross-season absolute episode numbers — each season
    has its own per-season episode numbering (1-13, 1-13, etc.), matching
    what the torrent filename actually says.

    Season 0 (Specials) is fetched last and only when regular seasons exist.

    Args:
        tv_id: TMDB show ID.
        language: Optional language override for episode names (e.g. ``"ja"``,
                  ``"zh-CN"``).  When empty, uses the client default (``"ja"``).
        season_numbers: Fetch only these provider seasons, including season zero.

    Returns:
        dict mapping season_number to {name, episodes: [{epNum, name, ...}]}
    """
    season_map: dict[int, dict] = {}

    # ── Fetch number_of_seasons from TV detail to bound iteration ──
    # The /tv/{id} endpoint returns number_of_seasons, avoiding the old
    # trial-and-error approach (range(1,31) with consecutive_empty heuristic).
    logger.debug("   📡 获取节目基本信息 (season count)...")
    try:
        detail = tv_detail if tv_detail is not None else (await tmdb_client.get_tv_detail(tv_id)).json()
        total_seasons = detail.get("number_of_seasons", 0)
    except Exception:
        if strict:
            raise
        detail = {}
        total_seasons = 0

    if total_seasons <= 0:
        # Fallback: if detail fetch fails, keep old behaviour
        total_seasons = 30

    # number_of_seasons does NOT include season 0 (Specials), so range
    # [1, total_seasons] already covers every regular season.
    logger.debug(f"   📡 TMDB 共 {total_seasons} 季，获取 S01–S{total_seasons:02d} 分季数据...")

    for s in sorted(season_numbers) if season_numbers is not None else range(1, total_seasons + 1):
        try:
            res = await tmdb_client.get_season_detail(tv_id, s, language=language)
        except Exception as exc:
            logger.warning(f"   ⚠️ S{s:02d} 请求失败: {exc}")
            if strict:
                raise
            continue

        try:
            data = res.json()
        except Exception:
            if strict:
                raise
            continue

        if not data or not data.get("episodes"):
            continue

        episodes = []
        filtered_count = 0
        for ep in data["episodes"]:
            ep_sn = ep.get("season_number")
            if ep_sn is not None and ep_sn != s and ep_sn > 0:
                # Skip episodes that belong to a *different* regular season
                # (keeps specials mixed in, and keeps all when season_number is
                # missing or 0 — TMDB occasionally omits/zeros this field)
                filtered_count += 1
                continue

            # Directors & writers
            directors = []
            writers = []
            for c in ep.get("crew", []):
                if c.get("job") == "Director":
                    directors.append(c["name"])
                if c.get("job") == "Writer":
                    writers.append(c["name"])

            # Guest stars / voice actors
            guest_stars = [
                {"name": gs["name"], "character": gs.get("character", "")}
                for gs in ep.get("guest_stars", [])
            ]

            episodes.append({
                "epNum": ep["episode_number"],
                "name": ep["name"],
                "tmdbId": ep["id"],
                "overview": ep.get("overview", ""),
                "airDate": ep.get("air_date", ""),
                "runtime": ep.get("runtime", 0),
                "stillPath": ep.get("still_path", ""),
                "voteAverage": ep.get("vote_average", 0),
                "voteCount": ep.get("vote_count"),
                "directors": directors,
                "writers": writers,
                "guestStars": guest_stars,
            })

        # Sort by episode number
        episodes.sort(key=lambda e: e["epNum"])

        if episodes:
            season_map[s] = {
                "name": data.get("name", f"Season {s}"),
                "episodes": episodes,
            }
            logger.debug(f"   ✅ S{s:02d}: {len(episodes)} 集 — {data.get('name', f'Season {s}')}")
        elif filtered_count > 0:
            logger.warning(f"   ⚠️ S{s:02d}: {filtered_count} 个剧集 season_number 不匹配，全部被过滤")

    # ── Fetch season 0 (Specials) — only if it exists ──
    has_s00 = any(
        s.get("season_number") == 0
        for s in detail.get("seasons", [])
    )
    if season_numbers is None and season_map and has_s00:
        try:
            res = await tmdb_client.get_season_detail(tv_id, 0, language=language)
            data = res.json()
            if data and data.get("episodes"):
                episodes = []
                for ep in data["episodes"]:
                    episodes.append({
                        "epNum": ep["episode_number"],
                        "name": ep["name"],
                        "tmdbId": ep["id"],
                        "overview": ep.get("overview", ""),
                        "airDate": ep.get("air_date", ""),
                        "runtime": ep.get("runtime", 0),
                        "stillPath": ep.get("still_path", ""),
                        "voteAverage": ep.get("vote_average", 0),
                        "voteCount": ep.get("vote_count"),
                    })
                episodes.sort(key=lambda e: e["epNum"])
                season_map[0] = {
                    "name": data.get("name", "Specials"),
                    "episodes": episodes,
                }
                logger.debug(f"   ✅ S00 (Specials): {len(episodes)} 集")
        except Exception:
            if strict:
                raise

    logger.debug(f"   📊 共获取 {len(season_map)} 个季: S{sorted(season_map.keys())}")
    return season_map
