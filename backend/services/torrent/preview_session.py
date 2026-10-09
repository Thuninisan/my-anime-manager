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
from ...domain.episode_metadata_adapters import provider_metadata_candidates
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


def candidate_context(key, entry, provider_catalogs):
    if "candidates" in entry and "show_key" in entry:
        return copy.deepcopy(entry)
    from ...domain.resource_adapters import provider_candidates
    tmdb = entry.get("tmdb") or {}
    bgm = entry.get("bangumi") or {}
    media_type = entry.get("media_type") or "tv"
    hints = entry.get("map_entries", [])
    candidates = copy.deepcopy(entry.get("candidates", {"tmdb": [], "bangumi": [], "tvdb": []}))
    for provider, evidence in entry.get("provider_recommendations", {}).items():
        candidates[provider].extend(evidence["candidates"])
    candidates["tmdb"].extend(provider_candidates("tmdb", [tmdb], media_type))
    candidates["bangumi"].extend(provider_candidates("bangumi", [bgm], media_type))
    for bid in entry.get("bangumi_ids", []):
        detail = provider_catalogs.get("bangumi", {}).get(str(bid), {})
        candidates["bangumi"].extend(provider_candidates("bangumi", [dict(detail, id=bid)], media_type, "existing_mapping"))
    candidates["tvdb"].extend(provider_candidates("tvdb", hints, media_type, "existing_mapping"))
    if media_type == "movie":
        candidates["tvdb"] = []
    for provider in candidates:
        candidates[provider] = deduplicate_candidates(candidates[provider])
    context = {"show_key": key, "display_name": tmdb.get("name") or bgm.get("name_cn") or bgm.get("name") or key,
                   "bangumi_display_name": bgm.get("name_cn") or bgm.get("name") or "",
                   "media_type": media_type, "candidates": candidates,
                   "mapping_hints": [{"bangumi_subject_id": h.get("bangumi_id"), "name": h.get("name", ""),
                       "tvdb_series_id": h.get("tvdb_id"), "tvdb_season_number": h.get("tvdb_season"),
                       "tmdb_season_number": h.get("tmdb_season")} for h in hints]}
    if not entry.get("discovery_complete"):
        discover_mapping_candidates(context)
    return context


def discover_mapping_candidates(context):
    """Mapping-table edges discover optional directories, never confirmed bindings."""
    from ... import data as data_store
    from ...domain.resource_adapters import provider_candidates
    movie = context["media_type"] == "movie"
    if movie:
        return
    links = []
    for candidate in context["candidates"]["tmdb"]:
        links.extend(data_store.get_map_entries_by_tmdb_id(candidate["provider_id"]))
    for candidate in context["candidates"]["bangumi"]:
        hint = data_store.get_map_entry(candidate["provider_id"])
        if hint:
            links.append(dict(hint, bangumi_id=candidate["provider_id"]))
    for hint in links:
        if (hint.get("tmdb_season") == -1) != movie:
            continue
        bid = hint.get("bangumi_id")
        if bid:
            context["candidates"]["bangumi"].extend(provider_candidates("bangumi", [dict(hint, id=bid)], context["media_type"], "existing_mapping"))
        if not movie:
            context["candidates"]["tvdb"].extend(provider_candidates("tvdb", [hint], "tv", "existing_mapping"))
            if hint.get("tmdb_id"):
                context["candidates"]["tmdb"].extend(provider_candidates("tmdb", [dict(hint, id=hint["tmdb_id"])], "tv", "existing_mapping"))
        normalized = {"bangumi_subject_id": bid, "name": hint.get("name", ""),
            "tvdb_series_id": None if movie else hint.get("tvdb_id"), "tvdb_season_number": hint.get("tvdb_season"),
            "tmdb_season_number": hint.get("tmdb_season")}
        if normalized not in context["mapping_hints"]:
            context["mapping_hints"].append(normalized)
    for provider in context["candidates"]:
        context["candidates"][provider] = deduplicate_candidates(context["candidates"][provider])


def build_snapshot(result: dict, source: Path) -> PreviewContextSnapshot:
    from .preview_files import unify_files
    result = unify_files(result)
    files = [{"file_id": file_id(item["torrent_path"]), "file_name": item["file_name"],
              "torrent_path": item["torrent_path"], "show_key": item["show_name"],
              "parsed": item["parsed_episode"], "type": item["type"],
              "category": item["category"], "processing_status": item["processing_status"],
              "skip_reason": item["skip_reason"]} for item in result["parsed_files"]]
    series = {}
    for key, entry in result.get("search_results", {}).items():
        series[key] = candidate_context(key, entry, result.get("provider_catalogs", {}))
    from .preview import _extract_year
    for item in files:
        if isinstance(item["show_key"], str) and item["show_key"] and item["show_key"] not in series:
            cleaned, _ = _extract_year(item["show_key"])
            if cleaned in series:
                item["show_key"] = cleaned
    return {"schema_version": PREVIEW_SCHEMA_VERSION,
            "torrent": {"name": result["torrent_name"], "source_path": str(source),
                        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "resource_id": result.get("resource_id")},
            "parsed_files": files, "series_contexts": series,
            "episode_catalog": episode_catalog(result.get("provider_catalogs", {})),
            "episode_metadata": normalize_metadata(result.get("provider_catalogs", {})),
            "episode_match_source": result.get("index", "tvdb")}


def deduplicate_candidates(candidates):
    unique = {}
    for candidate in candidates:
        key = (candidate["provider"], candidate["provider_id"])
        if key not in unique:
            unique[key] = candidate
        else:
            sources = set(unique[key]["source"].split("|")) | set(candidate["source"].split("|"))
            unique[key]["source"] = "|".join(sorted(sources))
    return list(unique.values())


def validate_resource_contexts(snapshot: PreviewContextSnapshot) -> None:
    for key, context in snapshot["series_contexts"].items():
        if context["show_key"] != key or context["media_type"] not in ("tv", "movie"):
            raise ValueError("invalid_preview_context")
        for provider, candidates in context["candidates"].items():
            for candidate in candidates:
                if candidate["provider"] != provider or candidate["provider_id"] <= 0:
                    raise ValueError("invalid_preview_candidate")


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
    try:
        snapshot = json.loads(row.context_json)
        if not isinstance(snapshot, dict):
            raise ValueError("invalid_preview_context")
        if snapshot.get("schema_version") != PREVIEW_SCHEMA_VERSION:
            raise HTTPException(409, "preview_schema_outdated")
        _SNAPSHOT_ADAPTER.validate_python(snapshot, strict=True)
        validate_resource_contexts(snapshot)
    except (ValidationError, ValueError, TypeError, KeyError):
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


def validate_mapping(snapshot: PreviewContextSnapshot, mapping: EpisodeMapping, context=None) -> None:
    try:
        for provider in ("tmdb", "tvdb", "bangumi"):
            ref = mapping[provider]
            series_id = ref["subject_id" if provider == "bangumi" else "series_id"]
            fields = ("episode_number", "episode_absolute") if provider == "bangumi" else ("season_number", "episode_number")
            values = [ref["episode_id"], *(ref[f] for f in fields)]
            if series_id is None:
                if any(v is not None for v in values):
                    raise ValueError()
                continue
            if context:
                candidate = next((c for c in context["candidates"][provider] if c["provider_id"] == series_id), None)
                if candidate is None or candidate["media_type"] not in (context["media_type"], "unknown", "special"):
                    raise HTTPException(422, "invalid_resource_identity")
            if context and context["media_type"] == "movie":
                if provider != "bangumi" or any(v is not None for v in values):
                    raise ValueError()
                continue
            if str(series_id) not in snapshot["episode_catalog"][provider]:
                raise ValueError()
            for field in fields:
                value = ref[field]
                if value is not None and (not math.isfinite(value) or value < 0):
                    raise ValueError()
            episodes = catalog_episodes(snapshot, provider, series_id)
            if ref["episode_id"] is not None:
                episodes = [ep for ep in episodes if ep["episode_id"] == ref["episode_id"]]
            if not any(all(ep[field] == ref[field] for field in fields) for ep in episodes):
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
            if source["type"] not in ("video", "subtitle") or source["processing_status"] == "ignored":
                raise HTTPException(422, "file_not_processable")
            if collection == "uploaded_subtitles" and source["type"] != "video":
                raise HTTPException(422, "invalid_subtitle_target")
            try:
                mapping = _MAPPING_ADAPTER.validate_python(item.get("mapping"), strict=True)
            except ValidationError:
                raise HTTPException(422, "invalid_episode_mapping")
            if mapping["match_source"] != snapshot["episode_match_source"]:
                raise HTTPException(409, "episode_match_source_conflict")
            series = snapshot["series_contexts"].get(source["show_key"], {})
            if not series:
                def belongs(context):
                    refs = [(mapping[p]["subject_id" if p == "bangumi" else "series_id"],
                             {c["provider_id"] for c in context["candidates"][p]}) for p in ("tmdb", "tvdb", "bangumi")]
                    movie_id = item.get("tmdb_movie_id")
                    return (any(value is not None for value, _ in refs)
                        and (movie_id is None or context["media_type"] == "movie" and movie_id in {c["provider_id"] for c in context["candidates"]["tmdb"]})
                        and all(value is None or value in allowed for value, allowed in refs))
                matching = [s for s in snapshot["series_contexts"].values() if belongs(s)]
                if len(matching) == 1:
                    series = matching[0]
            if not series:
                raise HTTPException(422, "invalid_resource_identity: select a series")
            if source["type"] != "subtitle":
                if mapping["bangumi"]["subject_id"] is None:
                    raise HTTPException(422, "bangumi_subject_required")
                if series.get("media_type") != "movie" and mapping["bangumi"]["episode_id"] is None:
                    raise HTTPException(422, "bangumi_episode_required")
            validate_mapping(snapshot, mapping, series)
            selected_series = series
            from ...domain.resource import resource_identity
            movie_id = item.get("tmdb_movie_id")
            if series.get("media_type") == "movie":
                if movie_id not in {c["provider_id"] for c in series["candidates"]["tmdb"] if c["media_type"] == "movie"}:
                    raise HTTPException(422, "invalid_movie_candidate")
            elif movie_id is not None:
                raise HTTPException(422, "invalid_movie_candidate")
            file_identity = resource_identity(series.get("media_type", "tv"), None,
                bangumi_subject_id=mapping["bangumi"]["subject_id"],
                tmdb_series_id=mapping["tmdb"]["series_id"],
                tvdb_series_id=mapping["tvdb"]["series_id"], tmdb_movie_id=movie_id) if series else None
            if series.get("media_type") != "movie" and source["type"] != "subtitle" and not any(
                    mapping[p]["season_number"] is not None and mapping[p]["episode_number"] is not None
                    and int(mapping[p]["episode_number"]) == mapping[p]["episode_number"] for p in (snapshot["episode_match_source"],)):
                raise HTTPException(422, "invalid_episode_mapping")
            movie_candidate = next((c for c in series.get("candidates", {}).get("tmdb", []) if c["provider_id"] == movie_id), {})
            entry = {"identity_revision": None, "resource_identity": file_identity, "episode_mapping": mapping, "torrent_path": source["torrent_path"],
                     "is_subtitle": source["type"] == "subtitle", "tmdb_show_name": movie_candidate.get("title") or next((c["title"] for c in series.get("candidates", {}).get("tmdb", []) if c["provider_id"] == mapping["tmdb"]["series_id"]), None) or selected_series.get("display_name", ""),
                     "bangumi_show_name": snapshot["episode_catalog"]["bangumi"].get(str(mapping["bangumi"]["subject_id"]), {}).get("name", selected_series.get("bangumi_display_name", "")),
                     **{k: item[k] for k in ("subtitle_suffix", "stored_filename", "original_filename") if k in item}}
            restored[collection].append(entry)
    selected_resources = [f.get("resource_identity") for f in restored["files"] if not f.get("is_subtitle")]
    movie_ids = {i["tmdb_movie_id"] for i in selected_resources if i and i["media_type"] == "movie"}
    movie_subjects = {i["bangumi_subject_id"] for i in selected_resources if i and i["media_type"] == "movie"}
    if movie_ids and len(movie_subjects) > 1:
        raise HTTPException(422, "ambiguous_resource: movie_subject_download")
    if movie_ids and (len(movie_ids) > 1 or any(i and i["media_type"] == "tv" for i in selected_resources)):
        raise HTTPException(422, "ambiguous_resource: mixed_movie_download")
    restored.update(torrent_path=snapshot["torrent"]["source_path"], torrent_name=snapshot["torrent"]["name"],
                    resource_id=None, preview_snapshot=snapshot)
    source = Path(snapshot["torrent"]["source_path"])
    if not source.is_file():
        raise HTTPException(410, "preview_source_missing")
    if hashlib.sha256(source.read_bytes()).hexdigest() != row.torrent_sha256:
        raise HTTPException(409, "preview_source_changed")
    touch_preview_session(row)
    return restored


async def augment_preview_session(preview_id: str, revision: int, show_key: str, provider: str, provider_id: int):
    row, snapshot = load_preview_session(preview_id, revision)
    if show_key not in snapshot["series_contexts"]:
        raise HTTPException(422, "invalid_show_key")
    if provider not in ("tmdb", "tvdb", "bangumi"):
        raise HTTPException(422, "invalid_preview_provider")
    if type(provider_id) is not int or provider_id <= 0:
        raise HTTPException(422, "invalid_preview_candidate")
    context = snapshot["series_contexts"][show_key]
    movie = context["media_type"] == "movie"
    if movie and provider == "tvdb":
        raise HTTPException(422, "unsupported_tvdb_movie")
    cached = str(provider_id) in snapshot["episode_catalog"][provider]
    existing = next((c for c in context["candidates"][provider] if c["provider_id"] == provider_id), None)
    data = {}
    detail = {}
    if existing and (cached or movie and provider == "tmdb"):
        detail = {"id": provider_id}
    elif provider == "tmdb":
        from ...clients import tmdb
        detail = (await (tmdb.get_movie_detail(provider_id) if movie else tmdb.get_tv_detail(provider_id))).json()
        if detail.get("id") != provider_id:
            raise HTTPException(422, "invalid_preview_candidate")
        if not movie and not cached:
            from ..tmdb import build_season_episode_map
            data = {"tmdb": {str(provider_id): await build_season_episode_map(provider_id, strict=True)}}
    elif provider == "tvdb":
        from ...clients import tvdb
        payload = (await tvdb.get_series(provider_id)).json()
        detail = payload.get("data") or {}
        if detail.get("id") != provider_id:
            raise HTTPException(422, "invalid_preview_candidate")
        from ..tvdb import fetch_tvdb_series_episodes
        directory = snapshot["episode_catalog"][provider].get(str(provider_id)) if cached else await fetch_tvdb_series_episodes(provider_id)
        if directory is None:
            raise HTTPException(502, "preview_provider_fetch_failed")
        if not cached:
            data = {"tvdb": {str(provider_id): directory}}
    else:
        from ...clients import bangumi
        detail = await bangumi.get_subject(provider_id)
        if detail.get("id", provider_id) != provider_id or not detail.get("name") and not detail.get("name_cn"):
            raise HTTPException(422, "invalid_preview_candidate")
        if detail.get("type", 2) != 2 or (movie and detail.get("platform") == "TV") or (not movie and detail.get("platform") == "剧场版"):
            raise HTTPException(422, "invalid_preview_candidate_media_type")
        detail = dict(detail, id=provider_id)
        if not cached:
            episodes = await bangumi.get_episodes(provider_id, ep_type=None)
            data = {"bangumi": {str(provider_id): {"name": detail.get("name_cn") or detail.get("name", ""),
                "episodes": [dict(ep, raw_sort=ep.get("sort")) for ep in episodes if ep.get("type") in (0, 1)]}}}
    from ...domain.resource_adapters import provider_candidates
    if not existing:
        existing = provider_candidates(provider, [detail], context["media_type"], "manual_candidate")[0]
        context["candidates"][provider].append(existing)
    if movie and provider == "tmdb":
        context["candidates"][provider] = [existing] + [c for c in context["candidates"][provider] if c["provider_id"] != provider_id]
    normalized = episode_catalog(data)
    for key in normalized:
        snapshot["episode_catalog"][key].update(normalized[key])
    snapshot["episode_metadata"].update(normalize_metadata(data))
    updated = update_preview_session(row, snapshot)
    from .preview_view import build_preview_view
    return build_preview_view(snapshot, updated.id, updated.revision, updated.expires_at)


def remove_preview_candidate(preview_id: str, revision: int, show_key: str, provider: str, provider_id: int):
    row, snapshot = load_preview_session(preview_id, revision)
    context = snapshot["series_contexts"].get(show_key)
    if not context or provider not in ("tmdb", "tvdb", "bangumi") or type(provider_id) is not int or provider_id <= 0:
        raise HTTPException(422, "invalid_preview_candidate")
    context["candidates"][provider] = [c for c in context["candidates"][provider] if c["provider_id"] != provider_id]
    updated = update_preview_session(row, snapshot)
    from .preview_view import build_preview_view
    return build_preview_view(snapshot, updated.id, updated.revision, updated.expires_at)


def set_preview_match_source(preview_id: str, revision: int, source: str):
    if source not in ("tmdb", "tvdb"):
        raise HTTPException(422, "invalid_episode_match_source")
    row, snapshot = load_preview_session(preview_id, revision)
    directories = snapshot["episode_catalog"].get(source, {})
    if not any(season["episodes"] for directory in directories.values()
               for season in (directory.get("seasons", {}) if source == "tvdb" else directory).values()):
        raise HTTPException(422, "episode_index_unavailable")
    snapshot["episode_match_source"] = source
    updated = update_preview_session(row, snapshot)
    from .preview_view import build_preview_view
    return build_preview_view(snapshot, updated.id, updated.revision, updated.expires_at)
