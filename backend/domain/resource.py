"""Resource identity is independent of episode coordinates and metadata policy."""
from typing import Literal
from typing_extensions import TypedDict


class ResourceCandidate(TypedDict):
    provider: Literal["bangumi", "tmdb", "tvdb"]
    provider_id: int
    media_type: Literal["tv", "movie", "special", "unknown"]
    title: str | None
    original_title: str | None
    alternative_titles: list[str]
    year: int | None
    source: str


class ResourceIdentity(TypedDict):
    media_type: Literal["tv", "movie"]
    canonical_title: str | None
    bangumi_subject_id: int | None
    tmdb_series_id: int | None
    tmdb_movie_id: int | None
    tvdb_series_id: int | None


class ResourceResolution(TypedDict):
    status: Literal["resolved", "ambiguous", "unresolved"]
    identity: ResourceIdentity | None
    candidates: list[ResourceCandidate]
    reason: str


def resource_identity(media_type="tv", canonical_title=None, **ids) -> ResourceIdentity:
    identity = dict(media_type=media_type, canonical_title=canonical_title,
                    bangumi_subject_id=None, tmdb_series_id=None,
                    tmdb_movie_id=None, tvdb_series_id=None)
    identity.update(ids)
    validate_resource_identity(identity)
    return identity


def validate_resource_identity(identity: ResourceIdentity) -> None:
    allowed = {"media_type", "canonical_title", "bangumi_subject_id", "tmdb_series_id", "tmdb_movie_id", "tvdb_series_id"}
    if set(identity) != allowed:
        raise ValueError("invalid_resource_identity: fields")
    if identity["canonical_title"] is not None and not isinstance(identity["canonical_title"], str):
        raise ValueError("invalid_resource_identity: canonical_title")
    if identity.get("media_type") not in ("tv", "movie"):
        raise ValueError("invalid_resource_identity: media_type")
    fields = ("bangumi_subject_id", "tmdb_series_id", "tmdb_movie_id", "tvdb_series_id")
    for field in fields:
        value = identity.get(field)
        if value is not None and (type(value) is not int or value <= 0):
            raise ValueError(f"invalid_resource_identity: {field}")
    if not any(identity.get(field) is not None for field in fields):
        raise ValueError("invalid_resource_identity: missing provider ID")
    if identity["media_type"] == "movie" and any(identity.get(field) is not None for field in ("tmdb_series_id", "tvdb_series_id")):
        raise ValueError("invalid_resource_identity: movie/series conflict")
    if identity["media_type"] == "tv" and identity.get("tmdb_movie_id") is not None:
        raise ValueError("invalid_resource_identity: tv/movie conflict")
