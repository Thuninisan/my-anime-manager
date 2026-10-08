"""Raw provider payloads end at this boundary. No network calls or ranking."""
from .resource import ResourceCandidate, resource_identity


def provider_candidates(provider: str, results: list[dict], media_type="tv", source=None) -> list[ResourceCandidate]:
    candidates = []
    for raw in results:
        value = raw.get("tvdb_id", raw.get("id")) if provider == "tvdb" else raw.get("id")
        try:
            provider_id = int(value)
        except (TypeError, ValueError):
            continue
        if provider_id <= 0 or isinstance(value, bool):
            continue
        date = raw.get("first_air_date") or raw.get("release_date") or raw.get("date") or raw.get("year") or ""
        try:
            year = int(str(date)[:4])
        except ValueError:
            year = None
        title = raw.get("name_cn") or raw.get("title") or raw.get("name")
        original = raw.get("original_title") or raw.get("original_name") or raw.get("name_original") or (raw.get("name") if provider == "bangumi" else None)
        aliases = raw.get("aliases") or raw.get("alternative_titles") or []
        if isinstance(aliases, dict):
            aliases = aliases.get("results", aliases.get("titles", []))
        aliases = [a if isinstance(a, str) else a.get("title", "") for a in aliases if isinstance(a, (str, dict))]
        candidates.append(dict(provider=provider, provider_id=provider_id, media_type=media_type,
                               title=title, original_title=original, alternative_titles=aliases,
                               year=year, source=source or f"{provider}_search"))
    return candidates


def provider_binding_identity(*, title=None, media_type="tv", bangumi_id=None, tmdb_id=None, tvdb_id=None):
    """Only legacy boundaries interpret 0 as missing; canonical validation rejects 0."""
    return resource_identity(media_type, title, bangumi_subject_id=bangumi_id or None,
                             tmdb_movie_id=(tmdb_id or None) if media_type == "movie" else None,
                             tmdb_series_id=(tmdb_id or None) if media_type == "tv" else None,
                             tvdb_series_id=(tvdb_id or None) if media_type == "tv" else None)


def search_entry_resolution(entry: dict, title: str):
    """Adapt a per-show legacy context; never borrow another show's provider IDs."""
    from ..services.resource_resolver import ResourceResolver, unique_provider_id
    if entry.get("resource_resolution") is not None:
        return entry["resource_resolution"]
    tmdb = entry.get("tmdb") or {}
    bgm = entry.get("bangumi") or {}
    media_type = entry.get("media_type") or "tv"
    hints = entry.get("map_entries", [])
    tvdb_id = unique_provider_id(h.get("tvdb_id") for h in hints)
    candidates = provider_candidates("tmdb", [tmdb], media_type) + provider_candidates("bangumi", [bgm], media_type)
    if not candidates and tvdb_id is None:
        return ResourceResolver().resolve([], title=title, media_type=media_type)
    identity = provider_binding_identity(title=tmdb.get("name") or bgm.get("name_cn") or bgm.get("name") or title,
                                    media_type=media_type, tmdb_id=tmdb.get("id"),
                                    bangumi_id=bgm.get("id"), tvdb_id=tvdb_id)
    return ResourceResolver().resolve(candidates, known=identity)


def subscription_identity(sub: dict, subject_id: int):
    """Repositories expose canonical bindings, including partial old DB views."""
    from .resource import validate_resource_identity
    identity = sub["resource_identity"]
    validate_resource_identity(identity)
    if identity["bangumi_subject_id"] != subject_id:
        raise ValueError("subscription_subject_identity_conflict")
    return dict(identity)


def select_provider_result(provider, results, title=None, *, year=None, media_type="tv", source=None):
    """Compatibility projection after a canonical decision; never chooses first."""
    from ..services.resource_resolver import ResourceResolver
    resolution = ResourceResolver().resolve(provider_candidates(provider, results, media_type, source),
                                            title=title, year=year, media_type=media_type)
    if resolution["status"] == "ambiguous":
        raise ValueError("ambiguous_resource")
    if resolution["identity"] is None:
        return None
    field = {"tmdb": "tmdb_movie_id" if media_type == "movie" else "tmdb_series_id",
             "tvdb": "tvdb_series_id", "bangumi": "bangumi_subject_id"}[provider]
    provider_id = resolution["identity"][field]
    return next(r for r in results if int(r.get("tvdb_id") or r.get("id")) == provider_id)



def provider_catalog_context(identity, entry: dict) -> dict:
    """Canonical → compatibility projection for the existing catalog orchestrator."""
    from .resource import validate_resource_identity
    validate_resource_identity(identity)
    result = dict(entry, resource_identity=identity, media_type=identity['media_type'])
    tmdb_id = identity['tmdb_series_id'] if identity['media_type'] == 'tv' else identity['tmdb_movie_id']
    result['tmdb'] = dict(entry.get('tmdb') or {}, id=tmdb_id) if tmdb_id is not None else None
    bid = identity['bangumi_subject_id']
    result['bangumi'] = dict(entry.get('bangumi') or {}, id=bid) if bid is not None else None
    return result


def monitor_resource_candidates(rows, index_type, *, title=None):
    """Persisted monitor results are candidate evidence, never confirmed identity."""
    candidates = []
    seen = set()
    for row in rows:
        if row.get("decision") == "excluded":
            continue
        media_type = "movie" if row.get("media_type", "TV").lower() == "movie" else "tv"
        for provider, key in (("bangumi", "bangumi_id"), (index_type, "index_id")):
            if provider not in ("bangumi", "tmdb", "tvdb"):
                continue
            adapted = provider_candidates(provider, [{"id": row.get(key), "name": title}],
                                          media_type, "resource_monitor_candidate")
            for candidate in adapted:
                marker = (provider, candidate["provider_id"], media_type)
                if marker not in seen:
                    candidates.append(candidate)
                    seen.add(marker)
    return candidates
