"""Resolve metadata once; matching and provider identity belong to EpisodeMapping."""
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .nfo.metadata_context import MetadataContext

logger = logging.getLogger(__name__)

from ..domain.episode import (
    EpisodeMapping, EpisodeMetadataCandidates, MetadataResolutionPolicy, ResolvedEpisode, EpisodeMetadata, empty_episode_metadata,
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


async def resolve_nfo_episode(
    mapping: EpisodeMapping, metadata_candidates: EpisodeMetadataCandidates,
    *, policy: MetadataResolutionPolicy = LEGACY_NFO_METADATA_POLICY,
    show_name: str = "", context: str = "episode.nfo", metadata_ctx: "MetadataContext | None" = None,
) -> ResolvedEpisode:
    """Legacy Chinese title/plot policy, including translation and language acquisition.

    All provider lookups use the supplied mapping coordinates; the context reuses
    preview/batch metadata. Existing translation helpers retain language validation.
    """
    from .nfo.translate import resolve_episode_title, is_chinese_plot, translate_ja_to_zh
    from .nfo.plot_fallback import resolve_episode_plot
    resolved = resolve_episode(mapping, metadata_candidates, policy)
    metadata = resolved["metadata"]
    bangumi = metadata_candidates["bangumi"]
    tmdb = metadata_candidates["tmdb"]
    if policy["translate_bangumi_title"]:
        title_source: list[str] = []
        metadata["title"] = await resolve_episode_title(
            bangumi["title"] if bangumi else "",
            (bangumi["original_title"] or "") if bangumi else "",
            tmdb["title"] if tmdb else "",
            show_name=show_name, context=context, selected_source=title_source,
        )
        resolved["provenance"]["title"] = _source_provenance(title_source[0]) if title_source else None
    metadata["original_title"] = (bangumi["original_title"] if bangumi else None) or metadata["title"]
    resolved["provenance"]["original_title"] = "bangumi" if bangumi and bangumi["original_title"] else resolved["provenance"]["title"]
    original_plot = metadata["plot"] or ""
    original_plot_source = resolved["provenance"]["plot"]
    if policy["require_chinese_plot"] and not is_chinese_plot(original_plot):
        sources: list[str] = []
        metadata["plot"] = ""
        try:
            tm, tv, bg = mapping["tmdb"], mapping["tvdb"], mapping["bangumi"]
            plot = await resolve_episode_plot(
                tmdb_id=tm["series_id"], tmdb_season=tm["season_number"], tmdb_ep_num=tm["episode_number"],
                tvdb_id=tv["series_id"], tvdb_season=tv["season_number"], tvdb_ep=tv["episode_number"],
                bangumi_id=bg["subject_id"], bangumi_sort=bg["episode_absolute"], bangumi_episode_id=bg["episode_id"],
                context=context, selected_source=sources, metadata_ctx=metadata_ctx, episode_mapping=mapping,
            )
            if not plot and original_plot:
                plot = await translate_ja_to_zh(original_plot)
                if plot:
                    sources.append(f"{original_plot_source.upper()} 原文 → DeepSeek" if original_plot_source is not None else "原始简介 → DeepSeek")
            metadata["plot"] = plot
        except Exception:
            logger.warning("Chinese episode plot resolution failed", exc_info=True)
        resolved["provenance"]["plot"] = _source_provenance(sources[0]) if sources else None
    _metadata_sources(metadata, resolved["provenance"])
    return resolved


def _source_provenance(source: str) -> str | None:
    """Keep provider and translation provenance explicit in canonical results."""
    if source.startswith("Bangumi"):
        return "bangumi:translated" if "译" in source or "DeepSeek" in source else "bangumi"
    if source.startswith("TMDB"):
        return "tmdb:translated" if "DeepSeek" in source else "tmdb"
    if source.startswith("TVDB"):
        return "tvdb:translated" if "DeepSeek" in source else "tvdb"
    return "translated" if "DeepSeek" in source else None


def _metadata_sources(metadata: EpisodeMetadata, provenance: dict[str, str | None]) -> None:
    for field, legacy_field in (("title", "title"), ("plot", "plot"), ("still_path", "thumbnail"), ("rating", "rating")):
        source = provenance.get(field)
        metadata["sources"][legacy_field] = "translated" if source is not None and (":" in source or source == "translated") else source
