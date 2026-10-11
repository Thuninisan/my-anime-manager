"""Shared mapping field ownership and binding-dependent invalidation."""

COMMUNITY_FIELDS = ("title_cn", "title_jp", "mikan_id", "tmdb_id", "tvdb_id",
                    "tmdb_season", "tvdb_season")
LOCAL_FIELDS = ("tmdb_series_title_cn", "tmdb_series_title_jp",
                "tvdb_series_title_cn", "tvdb_series_title_jp",
                "bangumi_subject_title_cn", "bangumi_subject_title_jp",
                "tmdb_ep_offset", "tvdb_ep_offset")
MAPPING_FIELDS = COMMUNITY_FIELDS + LOCAL_FIELDS


def merge_mapping(current: dict, changes: dict) -> dict:
    """Apply a partial update; never reuse numbering from a different binding."""
    merged = {**current, **changes}
    for provider in ("tmdb", "tvdb"):
        id_changed = current.get(f"{provider}_id") != merged.get(f"{provider}_id")
        if id_changed and f"{provider}_season" not in changes:
            merged[f"{provider}_season"] = 1 if provider == "tmdb" else None
        if provider == "tmdb" and merged.get("tmdb_season") is None:
            merged["tmdb_season"] = 1
        season_changed = current.get(f"{provider}_season") != merged.get(f"{provider}_season")
        invalidated = [f"{provider}_ep_offset"] if id_changed or season_changed else []
        if id_changed:
            invalidated.extend(f"{provider}_series_title_{lang}" for lang in ("cn", "jp"))
        for field in invalidated:
            if field not in changes:
                merged[field] = None
    return merged
