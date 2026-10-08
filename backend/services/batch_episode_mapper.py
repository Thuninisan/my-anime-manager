"""Batch input normalization, before metadata acquisition or writing."""
from ..domain.episode import EpisodeMapping
from ..domain.episode_metadata_adapters import legacy_batch_episode_mapping


def normalize_batch_episode(episode: dict) -> EpisodeMapping:
    """Explicit provider identity wins; never infer IDs from names or metadata."""
    mapping = legacy_batch_episode_mapping(episode)
    if not any(mapping[p][key] is not None for p, key in
               (("tmdb", "series_id"), ("tvdb", "series_id"), ("bangumi", "subject_id"))):
        raise ValueError("unresolved_episode: missing provider identity")
    from ..domain.resource_adapters import identity_from_legacy
    # Each entry has its own explicit identity; never consult another batch row.
    identity = identity_from_legacy(bangumi_id=mapping["bangumi"]["subject_id"],
                                    tmdb_id=mapping["tmdb"]["series_id"], tvdb_id=mapping["tvdb"]["series_id"])
    if episode.get("resource_identity") is not None and episode["resource_identity"] != identity:
        # Titles are display data; only provider bindings participate in validation.
        from ..domain.resource import validate_resource_identity
        validate_resource_identity(episode["resource_identity"])
        if any(episode["resource_identity"][key] != identity[key] for key in
               ("bangumi_subject_id", "tmdb_series_id", "tvdb_series_id", "tmdb_movie_id", "media_type")):
            raise ValueError("invalid_resource_identity: batch episode bindings")
    return mapping
