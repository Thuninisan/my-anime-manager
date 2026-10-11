"""Read and write provider coordinates on RSS download records."""

from ..domain.episode import create_episode_mapping
from ..domain.persistence import validate_episode_mapping

FIELDS = ("bangumi_episode_id", "bangumi_episode_number",
          "tmdb_series_id", "tmdb_episode_id", "tmdb_season_number", "tmdb_episode_number",
          "tvdb_series_id", "tvdb_episode_id", "tvdb_season_number", "tvdb_episode_number")


def as_dict(row):
    if row is None or not row.mapping_initialized:
        return None
    def number(value):
        return int(value) if isinstance(value, float) and value.is_integer() else value
    return {field: number(getattr(row, field)) for field in FIELDS}


def episode_mapping(bangumi_id, episode_number, values):
    """Compose the shared in-memory domain object without a snapshot envelope."""
    return create_episode_mapping(
        {"season_number": None, "episode_number": episode_number},
        {"subject_id": bangumi_id, "episode_absolute": episode_number,
         "episode_id": values.get("bangumi_episode_id"),
         "episode_number": values.get("bangumi_episode_number")},
        *[{key: values.get(f"{provider}_{key}") for key in
           ("series_id", "episode_id", "season_number", "episode_number")}
          for provider in ("tmdb", "tvdb")],
    )


def save(session, row, mapping):
    mapping = validate_episode_mapping(mapping)
    if (mapping["bangumi"]["subject_id"] != row.bangumi_id or
            mapping["bangumi"]["episode_absolute"] != row.episode_number):
        raise ValueError("download_episode_mapping_conflict")
    row.mapping_initialized = True
    for field in FIELDS:
        provider, key = field.split("_", 1)
        setattr(row, field, mapping[provider][key])
