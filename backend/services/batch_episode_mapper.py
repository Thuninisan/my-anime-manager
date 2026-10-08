"""Batch input normalization, before metadata acquisition or writing."""
from ..domain.episode import EpisodeMapping
from ..domain.episode_metadata_adapters import legacy_batch_episode_mapping


def normalize_batch_episode(episode: dict) -> EpisodeMapping:
    """Explicit provider identity wins; never infer IDs from names or metadata."""
    mapping = legacy_batch_episode_mapping(episode)
    if not any(mapping[p][key] is not None for p, key in
               (("tmdb", "series_id"), ("tvdb", "series_id"), ("bangumi", "subject_id"))):
        raise ValueError("unresolved_episode: missing provider identity")
    return mapping
