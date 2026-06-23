"""TMDB business logic layer."""

from ..clients import tmdb as tmdb_client

# TMDB genre ID for Animation
GENRE_ANIMATION = 16


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
    print(f"   {label}: [{len(items)}] {', '.join(names)}")


async def search_tv_show(
    show_name: str, prefer_year: str | None = None
) -> dict | None:
    """Search TMDB for a TV show, returning the best match.

    Filter pipeline:
    1. Single result → return immediately
    2. Multiple results → filter by Animation genre
    3. Still multiple → if year known, filter by year
    4. Still multiple → exact title match (name / original_name)
    5. Still multiple → highest popularity wins

    Args:
        show_name: Show name to search for
        prefer_year: Optional 4-digit year (extracted from torrent filename)

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

    # ── Step 1: only one result ──
    if len(results) == 1:
        show = results[0]
        print(
            f"   ✅ 唯一结果: {show['name']} "
            f"({show.get('original_name', '无原名')}) [id: {show['id']}]"
        )
        return _format_show(show)

    _print_candidates("原始结果", results)

    # ── Step 2: keep only Animation genre ──
    anime = [r for r in results if GENRE_ANIMATION in r.get("genre_ids", [])]
    if anime:
        candidates = anime
        if len(anime) < len(results):
            _print_candidates("过滤动画标签后", candidates)
    else:
        candidates = results
        print("   ⚠️ 无动画标签，保留全部结果")

    if len(candidates) == 1:
        show = candidates[0]
        print(
            f"   ✅ 动画过滤后唯一: {show['name']} "
            f"({show.get('original_name', '无原名')}) [id: {show['id']}]"
        )
        return _format_show(show)

    # ── Step 3: year match ──
    if prefer_year:
        year_matches = [
            r for r in candidates
            if (r.get("first_air_date", "") or "")[:4] == prefer_year
        ]
        if year_matches:
            candidates = year_matches
            _print_candidates(f"年份匹配 ({prefer_year})", candidates)
        else:
            print(f"   ⚠️ 无匹配年份 {prefer_year}，保留全部")

    if len(candidates) == 1:
        show = candidates[0]
        print(
            f"   ✅ 年份过滤后唯一: {show['name']} "
            f"({show.get('original_name', '无原名')}) [id: {show['id']}]"
        )
        return _format_show(show)

    # ── Step 4: exact title match (including all aliases) ──
    if len(candidates) > 1:
        q = show_name.lower()
        exact: list[dict] = []
        for r in candidates:
            # Check name and original_name from search results
            names = [
                (r.get("name") or "").lower(),
                (r.get("original_name") or "").lower(),
            ]
            # Also fetch alternative titles (lightweight, cached by TMDB)
            try:
                alt_res = await tmdb_client.get_alternative_titles(r["id"])
                for alt in alt_res.json().get("results", []):
                    title = (alt.get("title") or "").lower()
                    if title:
                        names.append(title)
            except Exception:
                pass  # Non-critical, skip if API fails

            if q in names:
                exact.append(r)

        if exact:
            candidates = exact
            _print_candidates("精确标题匹配(含别名)", candidates)

    if len(candidates) == 1:
        show = candidates[0]
        print(
            f"   ✅ 标题匹配唯一: {show['name']} "
            f"({show.get('original_name', '无原名')}) [id: {show['id']}]"
        )
        return _format_show(show)

    # ── Step 5: highest popularity ──
    show = max(candidates, key=lambda r: r.get("popularity", 0) or 0)
    print(
        f"   ✅ 热度最高: {show['name']} "
        f"({show.get('original_name', '无原名')}) "
        f"[id: {show['id']}, pop={show.get('popularity', 0):.1f}]"
    )
    return _format_show(show)


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

    Season 0 (Specials) is fetched last and only when regular seasons exist.

    Args:
        tv_id: TMDB show ID

    Returns:
        dict mapping season_number to {name, episodes: [{epNum, name, ...}]}
    """
    season_map: dict[int, dict] = {}

    print("   📡 使用默认 Season API 获取分季数据...")
    consecutive_empty = 0
    for s in range(1, 31):
        try:
            res = await tmdb_client.get_season_detail(tv_id, s)
        except Exception as exc:
            consecutive_empty += 1
            print(f"   ⚠️ S{s:02d} 请求失败: {exc}")
            if consecutive_empty >= 3:
                break  # 3 in a row → no more seasons
            continue

        try:
            data = res.json()
        except Exception:
            consecutive_empty += 1
            if consecutive_empty >= 3:
                break
            continue

        if not data or not data.get("episodes"):
            consecutive_empty += 1
            if consecutive_empty >= 3:
                break
            continue

        consecutive_empty = 0

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
            print(f"   ✅ S{s:02d}: {len(episodes)} 集 — {data.get('name', f'Season {s}')}")
        elif filtered_count > 0:
            print(f"   ⚠️ S{s:02d}: {filtered_count} 个剧集 season_number 不匹配，全部被过滤")

    # ── Fetch season 0 (Specials) ──
    if season_map:
        try:
            res = await tmdb_client.get_season_detail(tv_id, 0)
            data = res.json()
            if data and data.get("episodes"):
                episodes = []
                for ep in data["episodes"]:
                    directors = []
                    writers = []
                    for c in ep.get("crew", []):
                        if c.get("job") == "Director":
                            directors.append(c["name"])
                        if c.get("job") == "Writer":
                            writers.append(c["name"])
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
                episodes.sort(key=lambda e: e["epNum"])
                season_map[0] = {
                    "name": data.get("name", "Specials"),
                    "episodes": episodes,
                }
                print(f"   ✅ S00 (Specials): {len(episodes)} 集")
        except Exception as exc:
            print(f"   ⚠️ S00 (Specials) 请求失败: {exc}")

    print(f"   📊 共获取 {len(season_map)} 个季: S{sorted(season_map.keys())}")
    return season_map
