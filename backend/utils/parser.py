"""Filename parsing utilities using anitopy."""

from ..vendor import anitopy


def parse_filename(filename: str) -> dict | None:
    """Parse an anime video filename using anitopy.

    Handles all common anime naming conventions:
    - "[SubsPlease] Show Name S02E01-[1080p][BDRIP].mkv"
    - "[Group] Show Name - 12 [720p].mkv"
    - "Show Name S3 - 01 (1080p).mkv"

    Args:
        filename: Video filename

    Returns:
        dict with showName, season, episode keys, or None
    """
    try:
        info = anitopy.parse(filename)
    except Exception:
        return None

    title = (info.get("anime_title") or "").strip()
    if not title:
        return None

    ep_raw = info.get("episode_number")
    if not ep_raw:
        return None
    episode = int(ep_raw)

    season_raw = info.get("anime_season")
    season = int(season_raw) if season_raw else 1

    return {
        "showName": title,
        "season": season,
        "episode": episode,
    }
