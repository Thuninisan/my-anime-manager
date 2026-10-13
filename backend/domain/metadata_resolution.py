"""Select metadata fields and numbering without provider requests or file I/O."""

from .episode import (
    EpisodeMapping, EpisodeMetadataCandidates, MetadataResolutionPolicy,
    ResolvedEpisode, EpisodeMetadata, empty_episode_metadata,
)


LEGACY_NFO_METADATA_POLICY: MetadataResolutionPolicy = {
    "numbering_sources": ("tvdb", "tmdb"),
    "translate_bangumi_title": True,
    "require_chinese_plot": True,
    "field_sources": {
        "title": ("bangumi", "tmdb"),
        "original_title": ("bangumi",),
        "plot": ("tmdb", "tvdb"),
        "still_path": ("tmdb", "tvdb"),
        "thumbnail_url": ("tmdb", "tvdb"),
        "rating": ("tvdb", "tmdb"),
        "air_date": ("tmdb",),
        "runtime_minutes": ("tmdb",),
        "vote_count": ("tmdb",),
        "guest_stars": ("tmdb", "tvdb"),
        "actors": ("tmdb", "tvdb"),
        "directors": ("tmdb",),
        "writers": ("tmdb",),
    },
}


def resolve_episode(
    mapping: EpisodeMapping,
    metadata_candidates: EpisodeMetadataCandidates,
    policy: MetadataResolutionPolicy = LEGACY_NFO_METADATA_POLICY,
) -> ResolvedEpisode:
    """Select fields without fetching, matching, or changing the supplied identity.

    Empty strings/collections are missing metadata; numeric zero is a value.
    Coordinates are selected as an atomic pair, independently of match_source.
    """
    if mapping is None:
        raise ValueError("missing mapping: EpisodeMapping is required")
    if metadata_candidates is None:
        raise ValueError("missing metadata: EpisodeMetadataCandidates is required")
    metadata = empty_episode_metadata()
    provenance: dict[str, str | None] = {}
    for field, providers in policy["field_sources"].items():
        provenance[field] = None
        for provider in providers:
            candidate = metadata_candidates.get(provider)
            value = candidate.get(field) if candidate is not None else None
            if value is not None and value != "" and value != []:
                metadata[field] = value
                provenance[field] = provider
                break
    if not metadata["original_title"]:
        metadata["original_title"] = metadata["title"]
        provenance["original_title"] = provenance["title"]
    for provider in policy["numbering_sources"]:
        reference = mapping[provider]
        season_number, episode_number = reference["season_number"], reference["episode_number"]
        if season_number is not None and episode_number is not None:
            if int(episode_number) != episode_number:
                raise ValueError(f"required field: {provider} episode_number must be integral for NFO")
            provenance.update(season_number=provider, episode_number=provider)
            _metadata_sources(metadata, provenance)
            return {"mapping": mapping, "metadata": metadata,
                    "season_number": season_number, "episode_number": int(episode_number),
                    "provenance": provenance}
    raise ValueError("missing mapping required field: complete season_number/episode_number pair")


def _metadata_sources(metadata: EpisodeMetadata, provenance: dict[str, str | None]) -> None:
    for field, legacy_field in (("title", "title"), ("plot", "plot"), ("still_path", "thumbnail"), ("rating", "rating")):
        source = provenance.get(field)
        metadata["sources"][legacy_field] = "translated" if source is not None and (":" in source or source == "translated") else source
