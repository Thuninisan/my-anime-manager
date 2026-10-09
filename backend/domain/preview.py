"""Canonical, JSON-serializable short-lived torrent preview context."""
from typing import NotRequired
from typing_extensions import TypedDict
from pydantic import ConfigDict
from .resource import ResourceCandidate
from .episode import EpisodeCatalog, EpisodeMetadata, ParsedEpisodeRef, EpisodeMapping

PREVIEW_SCHEMA_VERSION = 3
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


class ProviderCandidates(TypedDict):
    tmdb: list[ResourceCandidate]
    bangumi: list[ResourceCandidate]
    tvdb: list[ResourceCandidate]


class SeriesContext(TypedDict):
    __pydantic_config__ = ConfigDict(extra="forbid")
    show_key: str
    display_name: str
    bangumi_display_name: str
    media_type: str
    candidates: ProviderCandidates
    mapping_hints: list[dict[str, str | int | None]]


class PreviewContextSnapshot(TypedDict):
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
    tmdb_movie_id: NotRequired[int]
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


class PreviewParsedFileView(TypedDict):
    file_id: str
    file_name: str
    torrent_path: str
    show_name: str
    parsed_episode: ParsedEpisodeRef


class PreviewSearchEntry(SeriesContext):
    pass


class TorrentPreviewResponse(TypedDict):
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
    episode_catalog: EpisodeCatalog
