"""Observation boundary for the existing torrent-directory scanner.

Series recognition and torrent-member matching stay in the existing preview
service. This scanner has no NFO/history/directory-coordinate inference.
"""
from typing_extensions import TypedDict
from ..domain.episode import EpisodeMapping
from .batch_episode_mapper import normalize_batch_episode


class ScannedEpisodeRef(TypedDict):
    torrent_path: str
    file_path: str
    file_name: str
    parsed_season_number: int | None
    parsed_episode_number: int | float | None


def resolve_scanned_episode(observation: ScannedEpisodeRef, matched_input: dict) -> EpisodeMapping:
    """Use explicit preview identity; observations cannot overwrite provider refs."""
    import copy
    mapping = copy.deepcopy(normalize_batch_episode(matched_input))
    mapping["parsed"] = {"season_number": observation["parsed_season_number"],
                         "episode_number": observation["parsed_episode_number"]}
    return mapping
