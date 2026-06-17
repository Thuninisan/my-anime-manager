"""SXXEXX input and filename parsing utilities."""

import re

from ..vendor import anitopy


def parse_input(input_str: str) -> dict | None:
    """Parse "ShowName SXXEXX" format input.

    Supports S01E12, s1e5, and similar variants.

    Args:
        input_str: User input string like "Show Name S01E12"

    Returns:
        dict with showName, season, episode keys, or None if parsing fails
    """
    trimmed = input_str.strip()
    match = re.match(r"^(.+?)\s+S(\d{1,4})\s*E(\d{1,4})$", trimmed, re.IGNORECASE)
    if not match:
        return None
    return {
        "showName": match.group(1).strip(),
        "season": int(match.group(2)),
        "episode": int(match.group(3)),
    }


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
