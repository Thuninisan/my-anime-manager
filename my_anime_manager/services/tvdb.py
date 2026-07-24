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
