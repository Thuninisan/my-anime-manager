"""Legacy NFO/provider boundaries. No matching or network requests."""
from typing import TYPE_CHECKING

from .episode import EpisodeMapping, EpisodeMetadataCandidates, create_episode_mapping
from .episode_adapters import merged_episode_metadata

if TYPE_CHECKING:
    from ..services.nfo.metadata_context import MetadataContext


def provider_metadata_candidates(
    tmdb: dict | None = None, tvdb: dict | None = None, bangumi: dict | None = None,
) -> EpisodeMetadataCandidates:
    result: EpisodeMetadataCandidates = {"tmdb": None, "tvdb": None, "bangumi": None}
    for provider, raw in (("tmdb", tmdb), ("tvdb", tvdb), ("bangumi", bangumi)):
        if raw is not None:
            metadata = merged_episode_metadata(raw)
            metadata["provider"] = provider
            metadata["provider_episode_id"] = raw.get(
                "tmdbId" if provider == "tmdb" else "tvdbId" if provider == "tvdb" else "id",
                raw.get("tvdb_ep_id") if provider == "tvdb" else raw.get("id"),
            )
            if provider == "tvdb":
                metadata["rating"] = raw.get("site_rating", raw.get("siteRating"))
            if provider == "bangumi":
                metadata["title"] = (raw.get("name_cn") or "").strip()
                metadata["original_title"] = (raw.get("name") or "").strip() or None
                metadata["plot"] = raw.get("desc")
            result[provider] = metadata
    return result


def legacy_batch_episode_mapping(episode: dict) -> EpisodeMapping:
    """Legacy batch coordinates are already decided by callers, including overrides."""
    if episode.get("episode_mapping") is not None:
        return episode["episode_mapping"]
    return create_episode_mapping(
        {"season_number": None, "episode_number": None},
        {"subject_id": episode.get("bangumi_subject_id"),
         "episode_id": episode.get("bangumi_ep_id"),
         "episode_number": episode.get("bangumi_episode_number"),
         "episode_absolute": episode.get("bangumi_episode_sort")},
        {"series_id": episode.get("tmdb_id"), "episode_id": episode.get("tmdb_ep_id"),
         "season_number": episode.get("tmdb_season"), "episode_number": episode.get("tmdb_episode")},
        {"series_id": episode.get("tvdb_id"), "episode_id": episode.get("tvdb_ep_id"),
         "season_number": episode.get("tvdb_season"), "episode_number": episode.get("tvdb_episode")},
    )


def bind_legacy_episode_ids(
    mapping: EpisodeMapping, candidates: EpisodeMetadataCandidates, bangumi_episodes: list[dict] | None = None,
) -> EpisodeMapping:
    """Enrich only legacy callers missing IDs after exact coordinate lookups."""
    import copy
    result = copy.deepcopy(mapping)
    for provider in ("tmdb", "tvdb", "bangumi"):
        metadata = candidates[provider]
        if result[provider]["episode_id"] is None and metadata is not None:
            result[provider]["episode_id"] = metadata["provider_episode_id"]
    if result["bangumi"]["episode_number"] is None and bangumi_episodes is not None:
        episode_id = result["bangumi"]["episode_id"]
        raw = next((episode for episode in bangumi_episodes if episode.get("id") == episode_id), {})
        result["bangumi"]["episode_number"] = raw.get("ep")
    return result


def legacy_download_episode_mapping(file: dict, preview_data: dict) -> EpisodeMapping:
    """Resolve old API references only at this boundary; refuse ambiguous series.

    New requests carry episode_mapping and never enter legacy series lookup.
    """
    if file.get("episode_mapping") is not None:
        return file["episode_mapping"]
    results = preview_data.get("search_results", {})
    matching = [entry for entry in results.values() if isinstance(entry, dict) and (
        file.get("bangumi_id") in entry.get("bangumi_ids", [])
        or (entry.get("bangumi") or {}).get("id") == file.get("bangumi_id")
        or (entry.get("tmdb") or {}).get("name") == file.get("tmdb_show_name")
    )]
    if not matching and len(results) == 1:
        matching = list(results.values())
    tmdb_ids = {(entry.get("tmdb") or {}).get("id") for entry in matching if (entry.get("tmdb") or {}).get("id") is not None}
    if len(tmdb_ids) > 1:
        raise ValueError("ambiguous provider identity: legacy TMDB series; submit episode_mapping")
    tvdb_ids = {item["tvdb_id"] for entry in matching for item in entry.get("map_entries", [])
                if item.get("bangumi_id") == file.get("bangumi_id") and item.get("tvdb_id") is not None}
    if len(tvdb_ids) > 1:
        raise ValueError("ambiguous provider identity: legacy TVDB series; submit episode_mapping")
    return legacy_batch_episode_mapping({
        "bangumi_subject_id": file.get("bangumi_id"), "bangumi_ep_id": file.get("bangumi_ep_id"),
        "bangumi_episode_sort": file.get("bangumi_sort"),
        "tmdb_id": next(iter(tmdb_ids), None), "tvdb_id": next(iter(tvdb_ids), None),
        "tmdb_season": file.get("tmdb_season"), "tmdb_episode": file.get("tmdb_episode"),
        "tvdb_season": file.get("tvdb_season"), "tvdb_episode": file.get("tvdb_episode"),
    })


def mapping_to_legacy_batch_episode(mapping: EpisodeMapping) -> dict:
    """The batch orchestrator still supports old RSS/batch inputs."""
    return {"episode_mapping": mapping,
            "bangumi_subject_id": mapping["bangumi"]["subject_id"],
            "bangumi_ep_id": mapping["bangumi"]["episode_id"],
            "bangumi_episode_number": mapping["bangumi"]["episode_number"],
            "tmdb_ep_id": mapping["tmdb"]["episode_id"],
            "tvdb_ep_id": mapping["tvdb"]["episode_id"],
            "bangumi_episode_sort": mapping["bangumi"]["episode_absolute"],
            "tmdb_id": mapping["tmdb"]["series_id"], "tvdb_id": mapping["tvdb"]["series_id"],
            "tmdb_season": mapping["tmdb"]["season_number"], "tmdb_episode": mapping["tmdb"]["episode_number"],
            "tvdb_season": mapping["tvdb"]["season_number"], "tvdb_episode": mapping["tvdb"]["episode_number"]}


def seed_preview_metadata(context: "MetadataContext", preview_data: dict) -> None:
    """Reuse preview provider payloads, avoiding a second full catalog download."""
    data = preview_data.get("episode_data", {})
    for series_id, seasons in data.get("tmdb", {}).items():
        context.tmdb_season_maps[(int(series_id), "zh-CN")] = seasons
    for series_id, series in data.get("tvdb", {}).items():
        context.tvdb_series[(int(series_id), "jpn")] = series
    for subject_id, subject in data.get("bangumi", {}).items():
        context.bgm_episodes[int(subject_id)] = subject.get("episodes", [])


def metadata_candidates_from_catalogs(
    mapping: EpisodeMapping, tmdb_seasons: dict, tvdb_series: dict,
    bangumi_episodes: list[dict], *, legacy: bool = False,
) -> EpisodeMetadataCandidates:
    """Select already matched references; never match titles or infer series IDs.

    Known episode IDs take precedence. Coordinate lookup supports older requests
    without episode IDs. The old cross-provider season lookup is legacy-only.
    """
    def provider_episode(provider: str, seasons: dict) -> dict | None:
        reference = mapping[provider]
        episode_id = reference["episode_id"]
        season_number = reference["season_number"]
        episode_number = reference["episode_number"]
        if episode_id is not None:
            # Find the exact object, even if the catalog's season differs. The
            # mapping still determines output numbering; metadata cannot alter it.
            for season in seasons.values():
                if not isinstance(season, dict):
                    continue
                for episode in season.get("episodes", []):
                    if episode.get("tmdbId" if provider == "tmdb" else "tvdbId") == episode_id:
                        return episode
            return None
        if legacy and provider == "tvdb" and season_number is None:
            season_number = mapping["tmdb"]["season_number"]
        if season_number is None or episode_number is None:
            return None
        season = seasons.get(str(season_number), seasons.get(season_number, {}))
        return next((episode for episode in season.get("episodes", [])
                     if episode.get("epNum") == episode_number), None)

    bangumi = mapping["bangumi"]
    bangumi_metadata = next((episode for episode in bangumi_episodes if (
        episode.get("id") == bangumi["episode_id"] if bangumi["episode_id"] is not None
        else bangumi["episode_absolute"] is not None and episode.get("raw_sort", episode.get("sort")) == bangumi["episode_absolute"]
    )), None)
    return provider_metadata_candidates(
        provider_episode("tmdb", tmdb_seasons),
        provider_episode("tvdb", tvdb_series.get("seasons", {})),
        bangumi_metadata,
    )


def download_entry_with_mapping(file: dict) -> dict:
    """Project canonical identity onto old processing fields at the API boundary."""
    mapping = file.get("episode_mapping")
    if mapping is None:
        return file
    result = dict(file)
    result.update(
        bangumi_id=mapping["bangumi"]["subject_id"] if mapping["bangumi"]["subject_id"] is not None else 0,
        bangumi_ep_id=mapping["bangumi"]["episode_id"],
        tmdb_season=mapping["tmdb"]["season_number"], tmdb_episode=mapping["tmdb"]["episode_number"],
        tvdb_season=mapping["tvdb"]["season_number"], tvdb_episode=mapping["tvdb"]["episode_number"],
    )
    if mapping["bangumi"]["episode_absolute"] is not None:
        result["bangumi_sort"] = mapping["bangumi"]["episode_absolute"]
    return result


def episode_path_parameters(file: dict) -> dict[str, int | float | None]:
    """Path arguments share the mapping used for NFO, without renumbering it."""
    file = download_entry_with_mapping(file)
    mapping = file.get("episode_mapping")
    bangumi_episode_number = mapping["bangumi"]["episode_number"] if mapping is not None else None
    return {"bangumi_sort": file.get("bangumi_sort"),
            "bangumi_ep": int(bangumi_episode_number) if bangumi_episode_number is not None else None,
            "tvdb_episode": file.get("tvdb_episode"), "tmdb_episode": file.get("tmdb_episode")}
