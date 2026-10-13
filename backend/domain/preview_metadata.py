"""Read metadata from canonical preview snapshots without session/database access."""

from .preview import PreviewContextSnapshot
from .episode import EpisodeMapping, EpisodeMetadataCandidates


def coordinate_metadata_key(provider: str, series_id: str | int | None, season_number: int | None, episode_number: float | None) -> str:
    coordinate = int(episode_number) if episode_number is not None and int(episode_number) == episode_number else episode_number
    return f"{provider}:coordinates:{series_id}:{season_number}:{coordinate}"


def catalog_episodes(snapshot: PreviewContextSnapshot, provider: str, series_id: int | None) -> list[dict]:
    entry = snapshot["episode_catalog"][provider].get(str(series_id), {})
    if provider == "bangumi":
        return entry.get("episodes", [])
    seasons = entry.get("seasons", {}) if provider == "tvdb" else entry
    return [ep for season in seasons.values() for ep in season["episodes"]]


def metadata_candidates(snapshot: PreviewContextSnapshot, mapping: EpisodeMapping) -> EpisodeMetadataCandidates:
    result = {"tmdb": None, "tvdb": None, "bangumi": None}
    for provider in result:
        ref = mapping[provider]
        episode_id = ref["episode_id"]
        if episode_id is None:
            # Preserve Phase 2 exact-coordinate overrides without mutating identity.
            series_id = ref["subject_id" if provider == "bangumi" else "series_id"]
            for ep in catalog_episodes(snapshot, provider, series_id):
                matches = (ref["episode_absolute"] is not None and ep["episode_absolute"] == ref["episode_absolute"]) if provider == "bangumi" else (
                    ref["season_number"] is not None and ref["episode_number"] is not None and
                    ep["season_number"] == ref["season_number"] and ep["episode_number"] == ref["episode_number"])
                if matches:
                    episode_id = ep["episode_id"]
                    break
        if episode_id is not None:
            result[provider] = snapshot["episode_metadata"].get(f"{provider}:{episode_id}")
        else:
            result[provider] = snapshot["episode_metadata"].get(coordinate_metadata_key(provider,
                ref["subject_id" if provider == "bangumi" else "series_id"],
                None if provider == "bangumi" else ref["season_number"],
                ref["episode_absolute"] if provider == "bangumi" else ref["episode_number"]))
    return result
