"""TVDB service layer — thin helpers over the TVDB v4 API client.

Unlike TMDB, TVDB usage is simple single-endpoint lookups; there is
no multi-step orchestration.  This module adds convenience wrappers
where the raw client responses would otherwise require in-line
filtering at every call site.
"""

import logging

from ..clients import tvdb as tvdb_client

logger = logging.getLogger(__name__)


async def get_episode(
    series_id: int,
    season: int,
    episode: int,
    *,
    language: str = "jpn",
) -> dict | None:
    """Fetch a single episode from TVDB by series, season and episode number.

    Uses ``get_series_episodes`` internally — one API call that returns
    all episodes across all seasons as a flat list.  The result is
    filtered in-process.

    Args:
        series_id: TVDB series ID.
        season: Season number.
        episode: Episode number within the season.
        language: Three-letter language code (default ``"jpn"``).

    Returns:
        Normalised dict with keys ``name``, ``overview``, ``air_date``,
        ``runtime``, ``still_path``, ``tvdb_ep_id``, or ``None`` when
        the episode is not found or the API call fails.
    """
    try:
        resp = await tvdb_client.get_series_episodes(
            series_id, language=language,
        )
    except Exception:
        logger.exception(
            "TVDB get_series_episodes failed (series=%d)", series_id,
        )
        return None

    payload = resp.json()
    data = payload.get("data", payload)
    episodes = data.get("episodes", [])

    for ep in episodes:
        if (ep.get("seasonNumber") == season
                and ep.get("number") == episode):
            return {
                "name": ep.get("name", ""),
                "overview": ep.get("overview", ""),
                "air_date": ep.get("aired", ""),
                "runtime": ep.get("runtime", 0),
                "still_path": ep.get("image", ""),
                "tvdb_ep_id": ep.get("id", 0),
                "site_rating": ep.get("siteRating") or 0,
            }

    logger.debug(
        "TVDB episode not found: series=%d S%dE%d",
        series_id, season, episode,
    )
    return None


async def fetch_tvdb_series_episodes(
    tvdb_id: int, series_name: str = "",
) -> dict | None:
    """Fetch all episodes for a TVDB series, grouped by season.

    Shared between the ktnbytes TMDB-first flow and the regular
    Bangumi-first flow.  Returns a structure suitable for
    ``episode_data.tvdb``.

    Args:
        tvdb_id: TVDB series ID.
        series_name: Fallback name if API call fails.

    Returns:
        ``{name, seasons: {season_num: {name, episodes: [...]}}}``
        or ``None`` on failure.
    """
    try:
        resp = await tvdb_client.get_series_episodes(tvdb_id, language="jpn")
    except Exception:
        print(f"   ⚠️ TVDB {tvdb_id} 剧集获取失败")
        return None

    payload = resp.json()
    data = payload.get("data", payload)
    raw_episodes: list[dict] = data.get("episodes", [])

    # Use series name from the first episode's seriesName if available
    ep_name = series_name
    if raw_episodes and not ep_name:
        ep_name = raw_episodes[0].get("seriesName", str(tvdb_id))

    # Group by season
    seasons: dict[int, dict] = {}
    for ep in raw_episodes:
        sn = ep.get("seasonNumber", 1)
        if sn not in seasons:
            seasons[sn] = {"name": f"Season {sn}", "episodes": []}
        seasons[sn]["episodes"].append({
            "epNum": ep.get("number", 0),
            "tvdbId": ep.get("id", 0),
            "name": ep.get("name", ""),
            "overview": ep.get("overview", ""),
            "airDate": ep.get("aired", ""),
            "runtime": ep.get("runtime", 0),
            "stillPath": ep.get("image", ""),
            "siteRating": ep.get("siteRating") or 0,
            "absoluteNumber": ep.get("absoluteNumber"),
        })

    # Sort episodes within each season
    for sn in seasons:
        seasons[sn]["episodes"].sort(key=lambda e: e["epNum"])

    print(f"   TVDB {tvdb_id} ({ep_name}): {len(seasons)} 季, {len(raw_episodes)} 集")
    return {"name": ep_name or str(tvdb_id), "seasons": seasons}
