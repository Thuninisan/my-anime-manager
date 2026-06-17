"""TMDB business logic layer."""

from ..clients import tmdb as tmdb_client


def _score_result(show: dict, query: str, prefer_year: str | None) -> float:
    """Score a TMDB search result against the query.

    Higher is better. Factors:
    1. Year match — if prefer_year matches first_air_date, big bonus
    2. Popularity — fallback when no year info
    """
    score = 0.0

    # Year match — strongest signal
    if prefer_year:
        first_air = show.get("first_air_date", "") or ""
        if first_air and first_air[:4] == prefer_year:
            score += 100

    # Popularity fallback
    score += (show.get("popularity", 0) or 0) * 0.01

    return score


async def search_tv_show(
    show_name: str, prefer_year: str | None = None
) -> dict | None:
    """Search for a TV show and return the best-matching result.

    Scores all results instead of blindly taking the first one.
    Matching factors: name similarity, year (from torrent filename), popularity.

    Args:
        show_name: Show name to search for
        prefer_year: Optional 4-digit year to prefer (extracted from torrent name)

    Returns:
        dict with id, name, original_name, first_air_date or None
    """
    print(f'🔍 TMDB 搜索: "{show_name}"')
    if prefer_year:
        print(f"   偏好年份: {prefer_year}")
    res = await tmdb_client.search_tv(show_name)
    results = res.json().get("results", [])
    if not results:
        print("   ❌ 无结果")
        return None

    # If only one result, no scoring needed
    if len(results) == 1:
        show = results[0]
        print(
            f"   ✅ 唯一结果: {show['name']} "
            f"({show.get('original_name', '无原名')}) [id: {show['id']}]"
        )
    else:
        # Score all results and pick best
        scored = sorted(
            results,
            key=lambda s: _score_result(s, show_name, prefer_year),
            reverse=True,
        )
        show = scored[0]
        print(
            f"   ✅ 最佳匹配: {show['name']} "
            f"({show.get('original_name', '无原名')}) [id: {show['id']}]"
        )
        # Show alternatives for debugging
        if len(scored) > 1:
            alts = [
                f"{s['name']} ({s.get('first_air_date', '?')[:4]})"
                for s in scored[1:4]
            ]
            print(f"   📋 其他结果: {', '.join(alts)}")

    return {
        "id": show["id"],
        "name": show["name"],
        "original_name": show.get("original_name"),
        "first_air_date": show.get("first_air_date"),
    }


async def get_tv_show_detail(tv_id: int) -> dict:
    """Get detailed TV show information.

    Args:
        tv_id: TMDB show ID

    Returns:
        dict with full show details including studios, genres
    """
    print("📡 获取 TMDB 详情...")
    res = await tmdb_client.get_tv_detail(tv_id)
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


async def build_season_episode_map(tv_id: int) -> dict[int, dict]:
    """Build a TMDB season→episodes mapping using the default Season API.

    Does NOT compute cross-season absolute episode numbers — each season
    has its own per-season episode numbering (1-13, 1-13, etc.), matching
    what the torrent filename actually says.

    Args:
        tv_id: TMDB show ID

    Returns:
        dict mapping season_number to {name, episodes: [{epNum, name, ...}]}
    """
    season_map: dict[int, dict] = {}

    print("   📡 使用默认 Season API 获取分季数据...")
    for s in range(1, 31):
        try:
            res = await tmdb_client.get_season_detail(tv_id, s)
            data = res.json()
        except Exception:
            break  # Request failed (usually season doesn't exist)

        if not data or not data.get("episodes"):
            break

        episodes = []
        for ep in data["episodes"]:
            if ep.get("season_number") != s:
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

        if not data.get("episodes"):
            break

    return season_map
