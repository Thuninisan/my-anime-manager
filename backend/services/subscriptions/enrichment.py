"""Enrich saved subscriptions without depending on the streaming HTTP protocol.

Cache lookup and refresh are separate so callers can report a cache hit before
fetching feeds. Full enrichment continues to use the existing provider engine.
"""

from collections.abc import Callable

from ... import data
from .. import rss as rss_service
from ..enrich import _compute_rss_offset, enrich_subscription

_ENRICH_GROUPS = ("bgm", "tvdb", "tmdb", "series_name", "resource_identity")


def copy_enrichment_fields(subscription: dict) -> dict:
    """Project response fields without asserting that they form a valid cache."""
    return {group: subscription[group] for group in _ENRICH_GROUPS if group in subscription}


def _find_subscription(bangumi_id: int) -> dict | None:
    return next((sub for sub in data.list_subscriptions()
                 if sub["bangumi_id"] == bangumi_id), None)


def get_cached_enrichment(bangumi_id: int) -> dict | None:
    """Return reusable enrichment, checking current community bindings and titles."""
    sub = _find_subscription(bangumi_id)
    if sub is None or not sub.get("bgm"):
        return None
    bgm = sub["bgm"]
    identity = sub["resource_identity"]
    has_eps = (bgm.get("sortrange") or [0, 0])[1] > 0
    has_tvdb = identity["tvdb_series_id"] is not None
    has_tmdb = identity["tmdb_series_id"] is not None or identity["tmdb_movie_id"] is not None
    # A partial cache must not hide community bindings added since enrichment.
    if identity["media_type"] == "tv" and (
        (not has_tmdb and data.get_tmdb_id(bangumi_id))
        or (not has_tvdb and data.get_tvdb_id(bangumi_id))
    ):
        return None
    if not (has_eps or has_tvdb or has_tmdb):
        return None
    mapping = data.get_mapping(bangumi_id)
    if not mapping.get("bangumi_subject_title_cn") and not mapping.get("bangumi_subject_title_jp"):
        return None
    for provider in ("tmdb", "tvdb"):
        if identity[f"{provider}_series_id"] is not None:
            if (mapping.get(f"{provider}_id") != identity[f"{provider}_series_id"]
                    or mapping.get(f"{provider}_ep_offset") is None
                    or not mapping.get(f"{provider}_series_title_cn")
                    or not mapping.get(f"{provider}_series_title_jp")):
                return None
    return copy_enrichment_fields(sub)


def _rss_urls(subscription: dict | None) -> tuple[str, str]:
    sub = subscription or {}
    return (sub.get("primary", {}).get("rss_url", ""),
            sub.get("backup", {}).get("rss_url", ""))


def _save_rss_offsets(bangumi_id: int, primary: int | None, backup: int | None) -> None:
    # Zero and negative offsets are valid; None must not overwrite saved values.
    if primary is not None:
        data.set_subscription_rss_offset(bangumi_id, "primary", primary)
    if backup is not None:
        data.set_subscription_rss_offset(bangumi_id, "backup", backup)


async def refresh_cached_enrichment(bangumi_id: int, cached: dict) -> dict:
    """Recompute source offsets even when platform enrichment can be reused."""
    primary_rss, backup_rss = _rss_urls(_find_subscription(bangumi_id))
    primary_offset: int | None = None
    backup_offset: int | None = None
    bgm_sortrange = cached.get("bgm", {}).get("sortrange")
    air_date = cached.get("bgm", {}).get("air_date", "")
    if bgm_sortrange and bgm_sortrange[0] > 0 and air_date:
        first_sort = bgm_sortrange[0]
        feeds = {}
        for url in dict.fromkeys(url for url in (primary_rss, backup_rss) if url):
            try:
                feeds[url] = await rss_service.fetch_rss_snapshot(url)
            except Exception:
                feeds[url] = {"items": []}
        if primary_rss:
            smallest = _compute_rss_offset(feeds[primary_rss], air_date)
            if smallest is not None:
                primary_offset = first_sort - smallest
        if backup_rss:
            smallest = _compute_rss_offset(feeds[backup_rss], air_date)
            if smallest is not None:
                backup_offset = first_sort - smallest

    _save_rss_offsets(bangumi_id, primary_offset, backup_offset)
    return {**cached, "primary_offset": primary_offset, "backup_offset": backup_offset}


def _save_enrichment(bangumi_id: int, enriched: dict) -> dict:
    fields = dict(enriched)
    primary_offset = fields.pop("primary_offset", None)
    backup_offset = fields.pop("backup_offset", None)
    # Keep expected mapping/identity revisions for the existing persistence checks.
    data.update_subscription(
        bangumi_id, fields,
        identity_source=(fields.get("resource_resolution") or {}).get("reason", "legacy_unknown"),
    )
    # Preserve the response snapshot timing and field shape of the original flow.
    result = _find_subscription(bangumi_id) or {}
    _save_rss_offsets(bangumi_id, primary_offset, backup_offset)
    result["primary_offset"] = primary_offset
    result["backup_offset"] = backup_offset
    return result


async def enrich_saved_subscription(
    bangumi_id: int, on_progress: Callable[[str], None] | None = None,
) -> dict | None:
    """Run full enrichment and persistence after the caller has checked the cache.

    A provider-engine failure still returns None. Persistence conflicts propagate
    to the caller; no transport event or HTTP exception is constructed here.
    """
    primary_rss, backup_rss = _rss_urls(_find_subscription(bangumi_id))
    result = await enrich_subscription(
        bangumi_id, on_progress=on_progress,
        primary_rss_url=primary_rss, backup_rss_url=backup_rss,
    )
    return _save_enrichment(bangumi_id, result) if result else result
