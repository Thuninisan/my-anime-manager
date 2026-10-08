"""Versioned permanent identity snapshots. No provider I/O or matching policy."""
import copy
import json
import math

from pydantic import TypeAdapter

from .episode import EpisodeMapping, create_episode_mapping
from .resource import validate_resource_identity

RESOURCE_IDENTITY_SCHEMA_VERSION = 1
EPISODE_MAPPING_SNAPSHOT_VERSION = 1
_MAPPING = TypeAdapter(EpisodeMapping)


def episode_mapping_snapshot(mapping, identity=None, *, source="canonical", revision=None, processing_result=None):
    mapping = _MAPPING.validate_python(mapping, strict=True)
    for provider in ("bangumi", "tmdb", "tvdb"):
        for key, value in mapping[provider].items():
            if value is None:
                continue
            if key.endswith("_id") and (type(value) is not int or value <= 0):
                raise ValueError("invalid_history_provider_id")
            if not key.endswith("_id") and (not math.isfinite(value) or value < 0):
                raise ValueError("invalid_history_coordinate")
    if revision is not None and (type(revision) is not int or revision < 1):
        raise ValueError("invalid_history_identity_revision")
    if identity is not None:
        validate_resource_identity(identity)
        # A history record cannot silently pair a mapping with a different work.
        pairs = (("tmdb_series_id", "tmdb", "series_id"),
                 ("tvdb_series_id", "tvdb", "series_id"))
        for field, provider, key in pairs:
            if mapping[provider][key] is not None and identity[field] != mapping[provider][key]:
                raise ValueError("history_resource_identity_conflict")
    return copy.deepcopy({"schema_version": EPISODE_MAPPING_SNAPSHOT_VERSION,
                          "resource_identity_schema_version": RESOURCE_IDENTITY_SCHEMA_VERSION,
                          "resource_identity": identity, "identity_revision": revision,
                          "source": source, "episode_mapping": mapping,
                          "processing_result": processing_result})


def load_episode_mapping_snapshot(raw):
    value = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(value, dict):
        raise ValueError("invalid_episode_mapping_snapshot")
    if value.get("schema_version") != EPISODE_MAPPING_SNAPSHOT_VERSION:
        raise ValueError("unsupported_episode_mapping_snapshot_version")
    if value.get("resource_identity_schema_version") != RESOURCE_IDENTITY_SCHEMA_VERSION:
        raise ValueError("unsupported_resource_identity_version")
    return episode_mapping_snapshot(value["episode_mapping"], value.get("resource_identity"),
                                    source=value.get("source", "legacy_unknown"),
                                    revision=value.get("identity_revision"),
                                    processing_result=value.get("processing_result"))


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


def history_snapshot(row):
    if row.episode_mapping_snapshot is not None:
        if row.episode_mapping_schema_version != EPISODE_MAPPING_SNAPSHOT_VERSION:
            raise ValueError("unsupported_episode_mapping_snapshot_version")
        return load_episode_mapping_snapshot(row.episode_mapping_snapshot)
    return legacy_history_to_episode_mapping(row)


def write_history_snapshot(row, mapping, identity=None, revision=None, processing_result=None):
    snapshot = episode_mapping_snapshot(mapping, identity, revision=revision, processing_result=processing_result)
    row.episode_mapping_snapshot = json.dumps(snapshot, ensure_ascii=False, allow_nan=False)
    row.episode_mapping_schema_version = EPISODE_MAPPING_SNAPSHOT_VERSION
    # Calculated fields are mirrors, while explicit overrides stay user configuration.
    row.tmdb_ep_calc = mapping["tmdb"]["episode_number"]
    row.tvdb_ep = mapping["tvdb"]["episode_number"]
