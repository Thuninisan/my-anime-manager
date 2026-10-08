from __future__ import annotations

"""Historical Phase 1–6 fixture builders, never imported by application code."""
from backend.domain.episode import EpisodeMapping, create_episode_mapping

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




def canonical_batch_fixture(episode):
    return {**episode, "episode_mapping": legacy_batch_episode_mapping(episode)}


def canonical_subscription_fixture(sub, subject_id):
    from backend.domain.resource_adapters import provider_binding_identity
    return {**sub, "resource_identity": provider_binding_identity(bangumi_id=subject_id,
        tmdb_id=sub.get("tmdb", {}).get("id"), tvdb_id=sub.get("tvdb", {}).get("id"))}
