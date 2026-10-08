"""Parser and provider normalization boundaries."""
from .episode import EpisodeCatalog, EpisodeMetadata, EpisodeMetadataSources, ParsedEpisodeRef


def parsed_episode_ref(file: dict) -> ParsedEpisodeRef:
    return {"season_number": file.get("season"), "episode_number": file.get("episode")}


def provider_episode_metadata(
    episode: dict,
    sources: EpisodeMetadataSources | None = None,
    *,
    thumbnail_base_url: str | None = None,
) -> EpisodeMetadata:
    """Normalize provider metadata without matching, I/O or identity inference."""
    thumbnail = episode.get("still_path", episode.get("stillPath")) or None
    if thumbnail and not thumbnail.startswith(("https://", "http://")):
        # Relative provider paths require an explicit provider base URL. Do not
        # guess its provider or put a path into the canonical URL field.
        thumbnail = thumbnail_base_url.rstrip("/") + "/" + thumbnail.lstrip("/") if thumbnail_base_url else None
    return {
        "provider": None,
        "vote_count": episode.get("voteCount", episode.get("vote_count")),
        "still_path": episode.get("still_path", episode.get("stillPath")),
        "provider_episode_id": episode.get("tmdbId", episode.get("tvdb_ep_id", episode.get("id"))),
        "guest_stars": [{"name": a if isinstance(a, str) else a.get("name", ""),
                         "character": None if isinstance(a, str) else a.get("character", a.get("role"))}
                        for a in (episode.get("guest_stars", episode.get("guestStars")) or [])],
        "title": episode.get("name") or "",
        "original_title": episode.get("original_name") or None,
        "plot": episode.get("overview") or None,
        "air_date": episode.get("airDate", episode.get("air_date")) or None,
        "runtime_minutes": episode.get("runtime"),
        "rating": episode.get("site_rating") if episode.get("site_rating") is not None else episode.get("voteAverage", episode.get("vote_average")),
        "thumbnail_url": thumbnail,
        "directors": list(episode.get("directors") or []),
        "writers": list(episode.get("writers") or []),
        "actors": [a if isinstance(a, str) else a.get("name", "") for a in (episode.get("guest_stars", episode.get("guestStars")) or [])],
        "sources": dict(sources) if sources is not None else {"title": None, "plot": None, "thumbnail": None, "rating": None},
    }


def episode_catalog(data: dict) -> EpisodeCatalog:
    """Normalize legacy provider catalogs at the preview/search boundary.

    Legacy provider_catalogs remains available for the existing NFO/download pipeline.
    ep and raw sort are independent; legacy sort's historical fallback stays in
    the legacy payload only.
    """
    result: EpisodeCatalog = {"tmdb": {}, "bangumi": {}, "tvdb": {}}
    for provider in ("tmdb", "tvdb"):
        for sid, entry in data.get(provider, {}).items():
            seasons = entry.get("seasons", {}) if provider == "tvdb" else entry
            output = {}
            for season, value in seasons.items():
                if not isinstance(value, dict) or "episodes" not in value:
                    continue
                output[str(season)] = {"name": value.get("name", ""), "episodes": [
                    {"series_id": int(sid), "episode_id": ep.get("tmdbId" if provider == "tmdb" else "tvdbId"),
                     "season_number": int(season), "episode_number": ep.get("epNum"),
                     "episode_absolute": ep.get("absoluteNumber"), "name": ep.get("name", ""),
                     **({"name_cn": ep["name_cn"]} if "name_cn" in ep else {})}
                    for ep in value.get("episodes", [])]}
            result[provider][str(sid)] = {"name": entry.get("name", ""), "seasons": output} if provider == "tvdb" else output
    for sid, entry in data.get("bangumi", {}).items():
        result["bangumi"][str(sid)] = {"name": entry.get("name", ""), "episodes": [
            {"subject_id": int(sid), "episode_id": ep.get("id"),
             "episode_number": ep.get("ep"), "episode_absolute": ep.get("raw_sort", ep.get("sort")),
             "matching_absolute": ep.get("sort"),
             "name": ep.get("name", ""), **({"name_cn": ep["name_cn"]} if "name_cn" in ep else {})}
            for ep in entry.get("episodes", [])]}
    return result


