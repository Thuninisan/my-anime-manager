"""Canonical, JSON-serializable short-lived torrent preview context."""
from typing import NotRequired
from typing_extensions import TypedDict
from pydantic import ConfigDict
from .resource import ResourceIdentity, ResourceResolution, ResourceCandidate
from .episode import EpisodeCatalog, EpisodeMetadata, ParsedEpisodeRef, EpisodeMapping

PREVIEW_SCHEMA_VERSION = 2
EpisodeMetadataStore = dict[str, EpisodeMetadata]


class TorrentPreviewSource(TypedDict):
    name: str
    source_path: str
    sha256: str
    resource_id: int | None


class PreviewParsedFile(TypedDict):
    file_id: str
    file_name: str
    torrent_path: str
    show_key: str
    parsed: ParsedEpisodeRef
    kind: str


class SeriesContext(TypedDict):
    # Each provider owns its unresolved evidence; aggregate identity keeps confirmed IDs.
    provider_resolutions: NotRequired[dict[str, ResourceResolution]]
    identity_revision: NotRequired[int | None]
    identity_source: NotRequired[str]
    resource_identity: ResourceIdentity | None
    resource_resolution: ResourceResolution
    show_key: str
    display_name: str
    bangumi_display_name: str
    media_type: str
    tmdb_series_id: int | None
    tmdb_movie_id: int | None
    tvdb_series_id: int | None
    bangumi_subject_id: int | None
    bangumi_subject_ids: list[int]
    mapping_hints: list[dict[str, str | int | None]]


class PreviewContextSnapshot(TypedDict):
    resource_candidates: NotRequired[list[ResourceCandidate]]
    schema_version: int
    torrent: TorrentPreviewSource
    parsed_files: list[PreviewParsedFile]
    series_contexts: dict[str, SeriesContext]
    episode_catalog: EpisodeCatalog
    episode_metadata: EpisodeMetadataStore
    skipped_files: list[dict[str, str]]
    episode_match_source: str


class PreviewDownloadFile(TypedDict):
    __pydantic_config__ = ConfigDict(extra="forbid")
    file_id: str
    mapping: EpisodeMapping
    subtitle_suffix: NotRequired[str]


class PreviewUploadedSubtitle(PreviewDownloadFile):
    stored_filename: str
    original_filename: str


class PreviewDownloadRequest(TypedDict):
    __pydantic_config__ = ConfigDict(extra="forbid")
    preview_id: str
    preview_revision: int
    files: list[PreviewDownloadFile]
    uploaded_subtitles: NotRequired[list[PreviewUploadedSubtitle]]
    resource_id: NotRequired[int]
    replace_bangumi_id: NotRequired[int]


class SeriesPreviewView(TypedDict):
    show_key: str
    display_name: str
    tmdb_series_id: int | None
    tmdb_movie_id: int | None
    tvdb_series_id: int | None
    bangumi_subject_id: int | None


class PreviewParsedFileView(TypedDict):
    file_id: str
    file_name: str
    torrent_path: str
    show_name: str
    parsed_episode: ParsedEpisodeRef


class PreviewSearchEntry(TypedDict):
    provider_resolutions: NotRequired[dict[str, ResourceResolution]]
    resource_identity: ResourceIdentity | None
    resource_resolution: ResourceResolution
    display_name: str
    bangumi_display_name: str
    tmdb_series_id: int | None
    tmdb_movie_id: int | None
    tvdb_series_id: int | None
    bangumi_subject_id: int | None
    media_type: str
    bangumi_subject_ids: list[int]
    mapping_hints: list[dict[str, str | int | None]]


class TorrentPreviewResponse(TypedDict):
    resource_candidates: NotRequired[list[ResourceCandidate]]
    preview_id: str
    revision: int
    expires_at: str
    torrent_name: str
    resource_id: int | None
    episode_match_source: str
    parsed_files: list[PreviewParsedFileView]
    specials: list[PreviewParsedFileView]
    subtitles: list[str]
    subtitle_files: list[PreviewParsedFileView]
    skipped_files: list[dict[str, str]]
    search_results: dict[str, PreviewSearchEntry]
    series: list[SeriesPreviewView]
    episode_catalog: EpisodeCatalog
