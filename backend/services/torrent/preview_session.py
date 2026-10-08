"""Database-owned preview lifecycle and the canonical download boundary."""
import copy
import hashlib
import json
import math
import posixpath
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from pydantic import TypeAdapter, ValidationError
from ... import config
from ...db import preview_sessions as repository
from ...db.models import TorrentPreviewSession
from ...domain.preview import PREVIEW_SCHEMA_VERSION, PreviewContextSnapshot, PreviewDownloadRequest
from ...domain.episode import EpisodeMapping, EpisodeMetadataCandidates
from ...domain.episode_adapters import episode_catalog
from ...domain.episode_metadata_adapters import provider_metadata_candidates, download_entry_with_mapping
from ...utils.paths import USER_DATA_DIR

PREVIEW_DIR = USER_DATA_DIR / "previews"
_MAPPING_ADAPTER = TypeAdapter(EpisodeMapping)
_DOWNLOAD_ADAPTER = TypeAdapter(PreviewDownloadRequest)
_SNAPSHOT_ADAPTER = TypeAdapter(PreviewContextSnapshot)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def expiry() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=config.PREVIEW_SESSION_TTL_HOURS)).isoformat()


def file_id(path: str) -> str:
    normalized = posixpath.normpath(path.replace("\\", "/"))
    return hashlib.sha256(normalized.encode()).hexdigest()[:24]


def coordinate_metadata_key(provider: str, series_id: str | int | None, season_number: int | None, episode_number: float | None) -> str:
    coordinate = int(episode_number) if episode_number is not None and int(episode_number) == episode_number else episode_number
    return f"{provider}:coordinates:{series_id}:{season_number}:{coordinate}"


def normalize_metadata(data: dict) -> dict:
    store = {}
    for provider in ("tmdb", "tvdb", "bangumi"):
        for series_id, entry in data.get(provider, {}).items():
            seasons = entry.get("seasons", {}) if provider == "tvdb" else entry
            items = [(None, ep) for ep in entry.get("episodes", [])] if provider == "bangumi" else [
                (int(number), ep) for number, season in seasons.items() if isinstance(season, dict)
                for ep in season.get("episodes", [])]
            for season_number, raw in items:
                metadata = provider_metadata_candidates(**{provider: raw})[provider]
                episode_id = metadata["provider_episode_id"]
                episode_number = raw.get("raw_sort", raw.get("sort")) if provider == "bangumi" else raw.get("epNum")
                key = f"{provider}:{episode_id}" if episode_id is not None else coordinate_metadata_key(
                    provider, series_id, season_number, episode_number)
                store[key] = metadata
    return store


def build_snapshot(result: dict, source: Path) -> PreviewContextSnapshot:
    files = []
    for key, kind in (("parsed_files", "video"), ("specials", "special")):
        for item in result.get(key, []):
            path = item["torrent_path"]
            files.append({"file_id": file_id(path), "file_name": item["file_name"],
                          "torrent_path": path, "show_key": item.get("show_name", ""),
                          "parsed": item.get("parsed_episode", {"season_number": item.get("season"),
                                                                "episode_number": item.get("episode")}),
                          "kind": kind})
    for path in result.get("subtitles", []):
        files.append({"file_id": file_id(path), "file_name": posixpath.basename(path),
                      "torrent_path": path, "show_key": "", "parsed": {
                          "season_number": None, "episode_number": None}, "kind": "subtitle"})
    series = {}
    for key, entry in result.get("search_results", {}).items():
        from ...domain.resource_adapters import search_entry_resolution
        resolution = search_entry_resolution(entry, key)
        identity = resolution["identity"]
        tmdb = entry.get("tmdb") or {}
        bgm = entry.get("bangumi") or {}
        hints = entry.get("map_entries", [])
        series[key] = {"identity_revision": entry.get("identity_revision"), "identity_source": entry.get("identity_source", resolution["reason"]), "resource_identity": identity, "resource_resolution": resolution, "show_key": key, "display_name": tmdb.get("name") or bgm.get("name_cn") or bgm.get("name") or key,
                       "bangumi_display_name": bgm.get("name_cn") or bgm.get("name") or "",
                       "media_type": identity["media_type"] if identity else entry.get("media_type") or "tv",
                       "tmdb_series_id": (identity["tmdb_series_id"] or identity["tmdb_movie_id"]) if identity else None,
                       "tvdb_series_id": identity["tvdb_series_id"] if identity else None,
                       "bangumi_subject_id": identity["bangumi_subject_id"] if identity else None, "bangumi_subject_ids": entry.get("bangumi_ids", []),
                       "mapping_hints": [{k: v for k, v in h.items() if k in {
                           "bangumi_id", "name", "name_original", "tvdb_id", "tvdb_season", "tmdb_season"}} for h in hints]}
    from .preview import _extract_year
    for item in files:
        if item["show_key"] and item["show_key"] not in series:
            cleaned, _ = _extract_year(item["show_key"])
            if cleaned in series:
                item["show_key"] = cleaned
    return {"schema_version": PREVIEW_SCHEMA_VERSION,
            "torrent": {"name": result["torrent_name"], "source_path": str(source),
                        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "resource_id": result.get("resource_id")},
            "parsed_files": files, "series_contexts": series,
            "episode_catalog": episode_catalog(result.get("episode_data", {})),
            "episode_metadata": normalize_metadata(result.get("episode_data", {})),
            "skipped_files": [{k: v for k, v in item.items() if k in {"file_name", "torrent_path", "reason"}}
                              for item in result.get("skipped_files", [])],
            "episode_match_source": result.get("index", "tvdb")}


def validate_resource_contexts(snapshot: PreviewContextSnapshot) -> None:
    from ...domain.resource import validate_resource_identity
    for context in snapshot["series_contexts"].values():
        identity = context.get("resource_identity")
        if identity is None:
            continue  # Existing v1 sessions use the explicit legacy context.
        validate_resource_identity(identity)
        tmdb_id = identity["tmdb_series_id"] if identity["media_type"] == "tv" else identity["tmdb_movie_id"]
        if (context["media_type"] != identity["media_type"] or context["tmdb_series_id"] != tmdb_id
            or context["tvdb_series_id"] != identity["tvdb_series_id"]
            or context["bangumi_subject_id"] != identity["bangumi_subject_id"]):
            raise ValueError("invalid_resource_identity: preview context conflict")


def create_preview_session(result: dict, source_path: str) -> TorrentPreviewSession:
    cleanup_expired_preview_sessions()
    if result.get("provider_fetch_errors"):
        raise HTTPException(502, "preview_provider_fetch_failed")
    snapshot = build_snapshot(result, Path(source_path))
    _SNAPSHOT_ADAPTER.validate_python(snapshot, strict=True)
    validate_resource_contexts(snapshot)
    preview_id = str(uuid4())
    directory = PREVIEW_DIR / preview_id
    directory.mkdir(parents=True)
    try:
        source = directory / "source.torrent"
        shutil.copyfile(source_path, source)
        snapshot["torrent"]["source_path"] = str(source)
        timestamp = now()
        row = TorrentPreviewSession(id=preview_id, schema_version=PREVIEW_SCHEMA_VERSION,
            revision=1, torrent_sha256=snapshot["torrent"]["sha256"], torrent_name=result["torrent_name"],
            context_json=json.dumps(snapshot, ensure_ascii=False), created_at=timestamp,
            updated_at=timestamp, last_used_at=timestamp, expires_at=expiry())
        repository.create(row)
        return repository.get(preview_id)
    except BaseException:
        shutil.rmtree(directory)
        raise


def load_preview_session(preview_id: str, revision: int | None = None) -> tuple[TorrentPreviewSession, PreviewContextSnapshot]:
    row = repository.get(preview_id)
    if row is None:
        raise HTTPException(404, "preview_not_found")
    if row.expires_at <= now():
        raise HTTPException(410, "preview_expired")
    if row.schema_version != PREVIEW_SCHEMA_VERSION:
        raise HTTPException(409, "preview_schema_outdated")
    snapshot = json.loads(row.context_json)
    if snapshot["schema_version"] != PREVIEW_SCHEMA_VERSION:
        raise HTTPException(409, "preview_schema_outdated")
    try:
        _SNAPSHOT_ADAPTER.validate_python(snapshot, strict=True)
        validate_resource_contexts(snapshot)
    except (ValidationError, ValueError):
        raise HTTPException(409, "preview_context_invalid")
    if revision is not None and row.revision != revision:
        raise HTTPException(409, "preview_revision_conflict")
    return row, snapshot


def touch_preview_session(row: TorrentPreviewSession) -> TorrentPreviewSession:
    if not repository.update(row.id, row.revision, last_used_at=now(), expires_at=expiry()):
        raise HTTPException(409, "preview_revision_conflict")
    return repository.get(row.id)


def update_preview_session(row: TorrentPreviewSession, snapshot: PreviewContextSnapshot) -> TorrentPreviewSession:
    _SNAPSHOT_ADAPTER.validate_python(snapshot, strict=True)
    validate_resource_contexts(snapshot)
    if not repository.update(row.id, row.revision, revision=row.revision + 1,
                             context_json=json.dumps(snapshot, ensure_ascii=False), updated_at=now(),
                             last_used_at=now(), expires_at=expiry()):
        raise HTTPException(409, "preview_revision_conflict")
    return repository.get(row.id)


def cleanup_expired_preview_sessions() -> int:
    ids = repository.cleanup_expired(now())
    for preview_id in ids:
        # Only service-owned directories can be removed; never a stored arbitrary path.
        shutil.rmtree(PREVIEW_DIR / preview_id, ignore_errors=True)
    return len(ids)


def catalog_episodes(snapshot: PreviewContextSnapshot, provider: str, series_id: int | None) -> list[dict]:
    entry = snapshot["episode_catalog"][provider].get(str(series_id), {})
    if provider == "bangumi":
        return entry.get("episodes", [])
    seasons = entry.get("seasons", {}) if provider == "tvdb" else entry
    return [ep for season in seasons.values() for ep in season["episodes"]]


def validate_mapping(snapshot: PreviewContextSnapshot, mapping: EpisodeMapping) -> None:
    try:
        for provider in ("tmdb", "tvdb", "bangumi"):
            ref = mapping[provider]
            series_id = ref["subject_id" if provider == "bangumi" else "series_id"]
            episode_id = ref["episode_id"]
            for field in ("episode_number", "episode_absolute") if provider == "bangumi" else ("season_number", "episode_number"):
                value = ref[field]
                if value is not None and (not math.isfinite(value) or value < 0):
                    raise ValueError()
            if series_id is not None and str(series_id) not in snapshot["episode_catalog"][provider]:
                # Movie identities are validated against series context, without invented episodes.
                identity_field = "bangumi_subject_id" if provider == "bangumi" else f"{provider}_series_id"
                if not any(s["media_type"] == "movie" and s[identity_field] == series_id
                           for s in snapshot["series_contexts"].values()):
                    raise ValueError()
            if episode_id is not None and not any(ep["episode_id"] == episode_id
                    for ep in catalog_episodes(snapshot, provider, series_id)):
                raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise HTTPException(422, "invalid_episode_mapping")


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


def restore_download_request(body: dict) -> dict:
    try:
        _DOWNLOAD_ADAPTER.validate_python(body, strict=True)
    except ValidationError as error:
        code = "invalid_episode_mapping" if any("mapping" in item["loc"] for item in error.errors()) else "invalid_preview_download"
        raise HTTPException(422, code)
    row, snapshot = load_preview_session(body["preview_id"], body.get("preview_revision"))
    if type(body.get("preview_revision")) is not int:
        raise HTTPException(422, "preview_revision_required")
    files_by_id = {item["file_id"]: item for item in snapshot["parsed_files"]}
    restored = copy.deepcopy(body)
    for collection in ("files", "uploaded_subtitles"):
        restored[collection] = []
        for item in body.get(collection, []):
            source = files_by_id.get(item.get("file_id"))
            if source is None:
                raise HTTPException(422, "invalid_file_id")
            try:
                mapping = _MAPPING_ADAPTER.validate_python(item.get("mapping"), strict=True)
            except ValidationError:
                raise HTTPException(422, "invalid_episode_mapping")
            validate_mapping(snapshot, mapping)
            series = snapshot["series_contexts"].get(source["show_key"], {})
            if not series:
                matching = [s for s in snapshot["series_contexts"].values() if
                            mapping["tmdb"]["series_id"] is not None and s["tmdb_series_id"] == mapping["tmdb"]["series_id"]]
                if len(matching) == 1:
                    series = matching[0]
            if series and source["kind"] != "subtitle":
                allowed_bgm = {series["bangumi_subject_id"], *series["bangumi_subject_ids"],
                               *(h.get("bangumi_id") for h in series["mapping_hints"])}
                allowed_tvdb = {series["tvdb_series_id"], *(h.get("tvdb_id") for h in series["mapping_hints"])}
                if (mapping["tmdb"]["series_id"] not in (None, series["tmdb_series_id"])
                    or mapping["tvdb"]["series_id"] not in allowed_tvdb
                    or mapping["bangumi"]["subject_id"] not in allowed_bgm):
                    raise HTTPException(422, "invalid_resource_identity")
            selected_series = series
            if series.get("media_type") != "movie" and source["kind"] != "subtitle" and not any(
                    mapping[p]["season_number"] is not None and mapping[p]["episode_number"] is not None
                    and int(mapping[p]["episode_number"]) == mapping[p]["episode_number"] for p in ("tmdb", "tvdb")):
                raise HTTPException(422, "invalid_episode_mapping")
            bgm_series = next((s for s in snapshot["series_contexts"].values()
                               if s["bangumi_subject_id"] == mapping["bangumi"]["subject_id"]), {})
            entry = {"identity_revision": selected_series.get("identity_revision"), "resource_identity": selected_series.get("resource_identity"), "episode_mapping": mapping, "torrent_path": source["torrent_path"],
                     "is_subtitle": source["kind"] == "subtitle", "tmdb_show_name": selected_series.get("display_name", ""),
                     "bangumi_show_name": snapshot["episode_catalog"]["bangumi"].get(str(mapping["bangumi"]["subject_id"]), {}).get("name", bgm_series.get("bangumi_display_name", "")),
                     "bangumi_sort": mapping["bangumi"]["episode_absolute"] if mapping["bangumi"]["episode_absolute"] is not None else mapping["parsed"]["episode_number"],
                     **{k: item[k] for k in ("subtitle_suffix", "stored_filename", "original_filename") if k in item}}
            restored[collection].append(download_entry_with_mapping(entry))
    selected_resources = [f.get("resource_identity") for f in restored["files"] if not f.get("is_subtitle")]
    movie_ids = {i["tmdb_movie_id"] for i in selected_resources if i and i["media_type"] == "movie"}
    if movie_ids and (len(movie_ids) > 1 or any(i and i["media_type"] == "tv" for i in selected_resources)):
        raise HTTPException(422, "ambiguous_resource: mixed_movie_download")
    restored.update(torrent_path=snapshot["torrent"]["source_path"], torrent_name=snapshot["torrent"]["name"],
                    resource_id=None, preview_data=private_nfo_context(snapshot))
    source = Path(snapshot["torrent"]["source_path"])
    if not source.is_file():
        raise HTTPException(410, "preview_source_missing")
    if hashlib.sha256(source.read_bytes()).hexdigest() != row.torrent_sha256:
        raise HTTPException(409, "preview_source_changed")
    touch_preview_session(row)
    return restored


def private_nfo_context(snapshot: PreviewContextSnapshot) -> dict:
    """Small legacy orchestration boundary, with canonical candidates held privately."""
    from .preview_view import search_views
    return {"search_results": search_views(snapshot), "episode_data": {"bangumi": snapshot["episode_catalog"]["bangumi"]},
            "canonical_snapshot": snapshot}


async def augment_preview_session(preview_id: str, revision: int, show_key: str, provider: str, provider_id: int):
    row, snapshot = load_preview_session(preview_id, revision)
    if show_key not in snapshot["series_contexts"]:
        raise HTTPException(422, "invalid_show_key")
    if type(provider_id) is not int or provider_id <= 0:
        raise HTTPException(422, "invalid_resource_identity")
    context = snapshot["series_contexts"][show_key]
    if context["media_type"] == "movie":
        if provider == "tvdb":
            raise HTTPException(422, "invalid_resource_identity: unsupported_tvdb_movie")
        if provider == "tmdb":
            from ...clients import tmdb
            detail = (await tmdb.get_movie_detail(provider_id)).json()
            from ...domain.resource_adapters import identity_from_legacy
            from ..resource_resolver import ResourceResolver
            identity = identity_from_legacy(title=detail.get("title") or context["display_name"], media_type="movie",
                                            tmdb_id=provider_id, bangumi_id=context["bangumi_subject_id"])
            context.update(tmdb_series_id=provider_id, display_name=identity["canonical_title"],
                           resource_identity=identity, resource_resolution=ResourceResolver().resolve([], known=identity))
            context["identity_revision"] = None
            context["identity_source"] = "explicit_user_mapping"
            context["resource_resolution"]["reason"] = "manual_provider_confirmation"
            updated = update_preview_session(row, snapshot)
            from .preview_view import build_preview_view
            return build_preview_view(snapshot, updated.id, updated.revision, updated.expires_at)
    if provider == "tmdb":
        from ..tmdb import build_season_episode_map
        data = {"tmdb": {str(provider_id): await build_season_episode_map(provider_id, strict=True)}}
    elif provider == "tvdb":
        from ..tvdb import fetch_tvdb_series_episodes
        series = await fetch_tvdb_series_episodes(provider_id)
        if series is None:
            raise HTTPException(502, "preview_provider_fetch_failed")
        data = {"tvdb": {str(provider_id): series}}
    elif provider == "bangumi":
        from ...clients import bangumi
        subject = await bangumi.get_subject(provider_id)
        episodes = await bangumi.get_episodes(provider_id, ep_type=None)
        data = {"bangumi": {str(provider_id): {"name": subject.get("name_cn") or subject.get("name", ""),
                  "episodes": [dict(ep, raw_sort=ep.get("sort")) for ep in episodes if ep.get("type") in (0, 1)]}}}
    else:
        raise HTTPException(422, "invalid_preview_provider")
    from ...domain.resource import resource_identity
    from ..resource_resolver import ResourceResolver
    context = snapshot["series_contexts"][show_key]
    field = {"tmdb": "tmdb_series_id", "tvdb": "tvdb_series_id", "bangumi": "bangumi_subject_id"}[provider]
    current = context.get("resource_identity")
    if current is None:
        current = resource_identity(context["media_type"], context["display_name"], **{field: provider_id})
    current = dict(current)
    current[field] = provider_id
    context[field] = provider_id
    if provider == "bangumi":
        context["bangumi_subject_ids"] = [provider_id]
        context["mapping_hints"] = []
        context["bangumi_display_name"] = data["bangumi"][str(provider_id)]["name"]
    context["resource_identity"] = current
    context["resource_resolution"] = ResourceResolver().resolve([], known=current)
    context["identity_revision"] = None
    context["identity_source"] = "explicit_user_mapping"
    context["resource_resolution"]["reason"] = "manual_provider_confirmation"
    normalized = episode_catalog(data)
    for key in normalized:
        snapshot["episode_catalog"][key].update(normalized[key])
    snapshot["episode_metadata"].update(normalize_metadata(data))
    updated = update_preview_session(row, snapshot)
    from .preview_view import build_preview_view
    return build_preview_view(snapshot, updated.id, updated.revision, updated.expires_at)
