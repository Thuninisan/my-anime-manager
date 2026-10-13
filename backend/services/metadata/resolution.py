"""Resolve metadata once; matching and provider identity belong to EpisodeMapping."""
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .context import MetadataContext

from ...domain.metadata_resolution import (
    LEGACY_NFO_METADATA_POLICY, resolve_episode, _metadata_sources,
)

logger = logging.getLogger(__name__)

from ...domain.episode import (
    EpisodeMapping, EpisodeMetadataCandidates, MetadataResolutionPolicy, ResolvedEpisode,
)


async def resolve_nfo_episode(
    mapping: EpisodeMapping, metadata_candidates: EpisodeMetadataCandidates,
    *, policy: MetadataResolutionPolicy = LEGACY_NFO_METADATA_POLICY,
    show_name: str = "", context: str = "episode.nfo", metadata_ctx: "MetadataContext | None" = None,
) -> ResolvedEpisode:
    """Legacy Chinese title/plot policy, including translation and language acquisition.

    All provider lookups use the supplied mapping coordinates; the context reuses
    preview/batch metadata. Existing translation helpers retain language validation.
    """
    from .translation import resolve_episode_title, is_chinese_plot, translate_ja_to_zh
    from .plots import resolve_episode_plot
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
                tvdb_id=tv["series_id"], tvdb_season=tv["season_number"], tvdb_episode_number=tv["episode_number"],
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
