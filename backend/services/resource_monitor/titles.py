"""Extract release-level clues from RSS titles without opening torrent files."""

import re

from ...vendor.anitopy import parse as anitopy_parse

RULE_VERSION = 3


def _numbers(value) -> list[int]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    return [int(str(item)) for item in values if str(item).isdigit()]


def parse_title(title: str, index_type: str) -> dict:
    raw = anitopy_parse(title) or {}
    names = raw.get("anime_title") or []
    names = names if isinstance(names, list) else [names]
    names = list(dict.fromkeys(alias.strip() for name in names
                               for alias in re.split(r"\s*/\s*", name) if alias.strip()))
    season_pattern = re.compile(r"(?i)\bS(?:eason)?\s*0*(\d+)(?:\s*([+-])\s*(?:S(?:eason)?)?\s*0*(\d+))?")
    season_matches = list(season_pattern.finditer(title))
    seasons = []
    for season_match in season_matches:
        first = int(season_match.group(1))
        last = int(season_match.group(3)) if season_match.group(3) else first
        seasons.extend(range(first, last + 1) if season_match.group(2) == "-" else [first, last])
    if season_matches:
        names = [re.sub(r"(?i)\s*S(?:eason)?\s*0*\d+(?:\s*[+-]\s*(?:S(?:eason)?)?\s*0*\d+)?\s*$", "", name).strip()
                 for name in names]
    movie = bool(re.search(r"(?i)\b(?:MOVIE|THE MOVIE)\b|劇場版", title))
    special_match = re.search(r"(?i)\bSP\s*[x×]\s*(\d+)\b", title)
    special_count = int(special_match.group(1)) if special_match else 0
    if not seasons and not movie:
        seasons = _numbers(raw.get("anime_season")) or [1]
    seasons = sorted(set(seasons))
    episode_match = next((match for match in re.finditer(r"(?<!\d)(\d{1,3})\s*([-+])\s*(\d{1,3})(?!\d)", title)
                          if not any(season.start() <= match.start() < season.end()
                                     for season in season_matches)), None)
    episode_range = None
    if episode_match:
        start, end = int(episode_match.group(1)), int(episode_match.group(3))
        if start <= end:
            episode_range = [start, end] if episode_match.group(2) == "-" else [start, end]
    elif _numbers(raw.get("episode_number")):
        numbers = _numbers(raw.get("episode_number"))
        episode_range = [min(numbers), max(numbers)]
    return {"raw_title": title, "raw_anitopy": raw, "name_candidates": names,
            "index_type": index_type, "seasons": seasons, "episode_range": episode_range,
            "whole_season": bool(seasons) and episode_range is None, "media_types": ["TV", "MOVIE"] if movie and seasons else ["MOVIE" if movie else "TV"],
            "special_count": special_count,
            "video_codec": raw.get("video_codec") or raw.get("video_term"), "resolution": raw.get("video_resolution"),
            "release_group": raw.get("release_group"), "rule_version": RULE_VERSION,
            "evidence": ["anitopy", "season-title-rule" if season_matches else "movie-only" if movie else "default-season-1",
                         "episode-title-rule" if episode_range and episode_match else "whole-season" if episode_range is None else "anitopy-episode",
                         *(["special-count-rule"] if special_match else [])]}
