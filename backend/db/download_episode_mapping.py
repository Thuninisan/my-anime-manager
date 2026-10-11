"""Relational RSS episode coordinates and legacy history conversion."""

from ..domain.episode import create_episode_mapping
from ..domain.persistence import load_episode_mapping_snapshot, validate_episode_mapping
from .models import DownloadEpisodeMapping

FIELDS = ("bangumi_episode_id", "bangumi_episode_number",
          "tmdb_series_id", "tmdb_episode_id", "tmdb_season_number", "tmdb_episode_number",
          "tvdb_series_id", "tvdb_episode_id", "tvdb_season_number", "tvdb_episode_number")


def as_dict(row):
    if row is None:
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
    target = session.get(DownloadEpisodeMapping, (row.bangumi_id, row.episode_number))
    if target is None:
        session.flush()  # The composite foreign key requires its parent first.
        target = DownloadEpisodeMapping(bangumi_id=row.bangumi_id, episode_number=row.episode_number)
        session.add(target)
    for field in FIELDS:
        provider, key = field.split("_", 1)
        setattr(target, field, mapping[provider][key])


def legacy_values(record, community):
    """Prefer recorded coordinates and explicit edits; fill missing IDs from mapping."""
    values = dict.fromkeys(FIELDS)
    path = record.get("download_path") or None
    raw = record.get("episode_mapping_snapshot")
    if raw is not None:
        snapshot = load_episode_mapping_snapshot(raw)
        mapping = snapshot["episode_mapping"]
        if (mapping["bangumi"]["subject_id"] not in (None, record["bangumi_id"]) or
                mapping["bangumi"]["episode_absolute"] not in (None, record["episode_number"])):
            raise ValueError("legacy_download_episode_mapping_conflict")
        for field in FIELDS:
            provider, key = field.split("_", 1)
            values[field] = mapping[provider][key]
        path = path or (snapshot.get("processing_result") or {}).get("target_path") or None
    for provider, legacy_ep in (("tmdb", "tmdb_ep_calc"), ("tvdb", "tvdb_ep")):
        id_field = f"{provider}_series_id"
        mapped_id = community.get(f"{provider}_id")
        if values[id_field] is None:
            values[id_field] = mapped_id
        if values[id_field] is not None and values[id_field] == mapped_id:
            season_field = f"{provider}_season_number"
            if values[season_field] is None:
                values[season_field] = community.get(f"{provider}_season")
        number_field = f"{provider}_episode_number"
        if values[number_field] is None:
            values[number_field] = record.get(legacy_ep)
    for old, new in (("tmdb_ep", "tmdb_episode_number"), ("tmdb_season", "tmdb_season_number")):
        if record.get(old) is not None:
            if values[new] != record[old]:
                values["tmdb_episode_id"] = None
            values[new] = record[old]
    return values, path
