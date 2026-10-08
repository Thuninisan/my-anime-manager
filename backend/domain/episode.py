"""JSON-friendly episode domain models; no provider requests or matching policy."""
from typing import Literal, NotRequired
from typing_extensions import TypedDict
from pydantic import ConfigDict

EpisodeMatchSource = Literal["tmdb", "tvdb"]
MetadataSource = Literal["bangumi", "tmdb", "tvdb", "translated"]


class ParsedEpisodeRef(TypedDict):
    __pydantic_config__ = ConfigDict(extra="forbid")
    season_number: int | None
    episode_number: int | float | None


class BangumiEpisodeRef(TypedDict):
    __pydantic_config__ = ConfigDict(extra="forbid")
    subject_id: int | None
    episode_id: int | None
    episode_number: int | float | None  # Bangumi API ep
    episode_absolute: int | float | None  # Bangumi API sort


class TmdbEpisodeRef(TypedDict):
    __pydantic_config__ = ConfigDict(extra="forbid")
    series_id: int | None
    episode_id: int | None
    season_number: int | None
    episode_number: int | float | None


class TvdbEpisodeRef(TmdbEpisodeRef):
    pass


class EpisodeMapping(TypedDict):
    __pydantic_config__ = ConfigDict(extra="forbid")
    parsed: ParsedEpisodeRef
    bangumi: BangumiEpisodeRef
    tmdb: TmdbEpisodeRef
    tvdb: TvdbEpisodeRef
    match_source: EpisodeMatchSource | None


class EpisodeMetadataSources(TypedDict):
    title: MetadataSource | None
    plot: MetadataSource | None
    thumbnail: MetadataSource | None
    rating: MetadataSource | None


class EpisodeActor(TypedDict):
    name: str
    character: str | None


class EpisodeMetadata(TypedDict):
    provider: MetadataSource | None
    title: str
    original_title: str | None
    plot: str | None
    air_date: str | None
    runtime_minutes: int | None
    rating: float | None
    vote_count: int | None
    still_path: str | None
    thumbnail_url: str | None
    provider_episode_id: int | None
    directors: list[str]
    writers: list[str]
    actors: list[str]
    guest_stars: list[EpisodeActor]
    sources: EpisodeMetadataSources


class EpisodeMetadataCandidates(TypedDict):
    bangumi: EpisodeMetadata | None
    tmdb: EpisodeMetadata | None
    tvdb: EpisodeMetadata | None


class ResolvedEpisode(TypedDict):
    mapping: EpisodeMapping
    metadata: EpisodeMetadata
    season_number: int
    episode_number: int
    provenance: dict[str, str | None]


class MetadataResolutionPolicy(TypedDict):
    field_sources: dict[str, tuple[MetadataSource, ...]]
    numbering_sources: tuple[EpisodeMatchSource, ...]
    translate_bangumi_title: bool
    require_chinese_plot: bool


class SeriesMetadata(TypedDict):
    title: str
    original_title: str | None
    plot: str | None
    genres: list[str]
    studios: list[str]
    status: str | None
    poster_url: str | None
    fanart_url: str | None



class CatalogEpisode(TmdbEpisodeRef):
    name: str
    name_cn: NotRequired[str]
    episode_absolute: int | float | None


class CatalogSeason(TypedDict):
    name: str
    episodes: list[CatalogEpisode]


class BangumiCatalogEpisode(BangumiEpisodeRef):
    matching_absolute: NotRequired[int | float | None]
    name: str
    name_cn: NotRequired[str]


class BangumiCatalogEntry(TypedDict):
    name: str
    episodes: list[BangumiCatalogEpisode]


class TvdbCatalogEntry(TypedDict):
    name: str
    seasons: dict[str, CatalogSeason]


class EpisodeCatalog(TypedDict):
    tmdb: dict[str, dict[str, CatalogSeason]]
    bangumi: dict[str, BangumiCatalogEntry]
    tvdb: dict[str, TvdbCatalogEntry]


def create_episode_mapping(
    parsed: ParsedEpisodeRef,
    bangumi: BangumiEpisodeRef | None = None,
    tmdb: TmdbEpisodeRef | None = None,
    tvdb: TvdbEpisodeRef | None = None,
    match_source: EpisodeMatchSource | None = None,
) -> EpisodeMapping:
    """Compose coordinates without metadata, fallback, or matching policy."""
    def reference(value: TmdbEpisodeRef | None) -> TmdbEpisodeRef:
        return {
            "series_id": value["series_id"] if value is not None else None,
            "episode_id": value["episode_id"] if value is not None else None,
            "season_number": value["season_number"] if value is not None else None,
            "episode_number": value["episode_number"] if value is not None else None,
        }

    return {
        "parsed": dict(parsed),
        "bangumi": dict(bangumi) if bangumi is not None else {
            "subject_id": None, "episode_id": None,
            "episode_number": None, "episode_absolute": None,
        },
        "tmdb": reference(tmdb),
        "tvdb": reference(tvdb),
        "match_source": match_source,
    }


def empty_episode_metadata() -> EpisodeMetadata:
    """An explicit empty candidate, with numeric and identity fields absent."""
    return {
        "provider": None, "title": "", "original_title": None, "plot": None,
        "air_date": None, "runtime_minutes": None, "rating": None, "vote_count": None,
        "still_path": None, "thumbnail_url": None, "provider_episode_id": None,
        "directors": [], "writers": [], "actors": [], "guest_stars": [],
        "sources": {"title": None, "plot": None, "thumbnail": None, "rating": None},
    }
