"""Conservative read-only view of the facts stored on an old history row."""
import math
from ..domain.episode import create_episode_mapping
from ..domain.persistence import episode_mapping_snapshot


def legacy_history_to_episode_mapping(row):
    """Use only the row's facts. A current binding is never historical evidence.

    tmdb_ep/season are mutable user overrides, hence are NOT evidence of the
    original download. tmdb_ep_calc and tvdb_ep are recorded calculated numbers.
    """
    def coordinate(value):
        return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None

    subject = row.bangumi_id if type(row.bangumi_id) is int and row.bangumi_id > 0 else None
    sort = coordinate(row.episode_number)
    mapping = create_episode_mapping(
        {"season_number": None, "episode_number": sort},
        {"subject_id": subject, "episode_id": None, "episode_number": None,
         "episode_absolute": sort},
        {"series_id": None, "episode_id": None, "season_number": None,
         "episode_number": coordinate(row.tmdb_ep_calc)},
        {"series_id": None, "episode_id": None, "season_number": None,
         "episode_number": coordinate(row.tvdb_ep)},
    )
    return episode_mapping_snapshot(mapping, source="legacy_unresolved")


