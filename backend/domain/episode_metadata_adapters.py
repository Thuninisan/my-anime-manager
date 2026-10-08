"""Provider metadata normalization boundaries. No matching or network requests."""
from .episode import EpisodeMapping, EpisodeMetadataCandidates
from .episode_adapters import provider_episode_metadata


def provider_metadata_candidates(
    tmdb: dict | None = None, tvdb: dict | None = None, bangumi: dict | None = None,
) -> EpisodeMetadataCandidates:
    result: EpisodeMetadataCandidates = {"tmdb": None, "tvdb": None, "bangumi": None}
    for provider, raw in (("tmdb", tmdb), ("tvdb", tvdb), ("bangumi", bangumi)):
        if raw is not None:
            metadata = provider_episode_metadata(raw)
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


def metadata_candidates_from_catalogs(
    mapping: EpisodeMapping, tmdb_seasons: dict, tvdb_series: dict,
    bangumi_episodes: list[dict],
) -> EpisodeMetadataCandidates:
    """Select already matched references; never match titles or infer series IDs.

    Known episode IDs take precedence. Coordinate lookup supports older requests
    without episode IDs; no cross-provider coordinate inference.
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


def episode_path_parameters(file: dict) -> dict[str, int | float | None]:
    """Only project canonical coordinates into the user path-template vocabulary."""
    mapping = file["episode_mapping"]
    number = mapping["bangumi"]["episode_number"]
    return {"bangumi_sort": mapping["bangumi"]["episode_absolute"],
            "bangumi_ep": int(number) if number is not None else None,
            "tvdb_episode": mapping["tvdb"]["episode_number"],
            "tmdb_episode": mapping["tmdb"]["episode_number"]}


def seed_provider_catalogs(context, data: dict) -> None:
    """Provider acquisition boundary for request-local metadata caches."""
    for series_id, seasons in data.get("tmdb", {}).items():
        context.tmdb_season_maps[(int(series_id), "zh-CN")] = seasons
    for series_id, series in data.get("tvdb", {}).items():
        context.tvdb_series[(int(series_id), "jpn")] = series
    for subject_id, subject in data.get("bangumi", {}).items():
        context.bgm_episodes[int(subject_id)] = subject.get("episodes", [])
