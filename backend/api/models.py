"""API request/response Pydantic models."""

from pydantic import BaseModel, ConfigDict
from ..domain.resource import ResourceIdentity

# ═══════════════════════════════════════════════════════════════════════

class TmdbEpisodeInfo(BaseModel):
    epNum: int
    name: str
    tmdbId: int
    overview: str = ""
    airDate: str = ""
    runtime: int = 0
    stillPath: str = ""


class SeasonInfo(BaseModel):
    name: str
    episodes: list[TmdbEpisodeInfo]


class RssSubtitleGroup(BaseModel):
    name: str
    subgroup_id: int
    rss_url: str


class BangumiRssResponse(BaseModel):
    bangumi_id: int
    name: str
    mikan_id: int
    global_rss: str
    groups: list[RssSubtitleGroup]


class MikanSearchResult(BaseModel):
    mikan_id: int
    title: str
    url: str


class AssignMikanRequest(BaseModel):
    mikan_id: int


class ManualSubscribeIn(BaseModel):
    name: str
    rss_url: str
    bangumi_id: int
    backup_rss_url: str = ""


class RssFeedItem(BaseModel):
    guid: str
    title: str
    torrent_url: str
    pub_date: str
    size_bytes: int
    downloaded: bool
    tags: list[str]
    passed: bool
    excluded: bool
    episode_number: int = 0


class RssFeedResponse(BaseModel):
    title: str
    items: list[RssFeedItem]


class SubscriptionIn(BaseModel):
    name: str
    rss_url: str
    bangumi_id: int
    subgroup_id: int
    subgroup_name: str
    filter_tags: list[str] = []
    backup_rss_url: str = ""
    backup_subgroup_id: int = 0
    backup_subgroup_name: str = ""
    backup_filter_tags: list[str] = []
    download_path: str = ""
    active: int = 1
    exclude_patterns: list[str] = []
    backup_exclude_patterns: list[str] = []


class BgmMeta(BaseModel):
    season: int = 1
    sortrange: list[int] = []
    subject_name: str = ""
    subject_title_cn: str | None = None
    subject_title_jp: str | None = None
    rating: float = 0.0
    air_date: str = ""


class TvdbMeta(BaseModel):
    season: int | None = None
    # 平台季内集数 = Bangumi sort + ep_offset；None=未知，0=确认相同。
    ep_offset: int | None = None
    series_title_cn: str | None = None
    series_title_jp: str | None = None


class TmdbMeta(BaseModel):
    season: int | None = None
    ep_offset: int | None = None
    series_title_cn: str | None = None
    series_title_jp: str | None = None


class RssSource(BaseModel):
    rss_url: str = ""
    subgroup_id: int = 0
    subgroup_name: str = ""
    filter_tags: list[str] = []
    exclude_patterns: list[str] = []


class SubscriptionOut(BaseModel):
    resource_identity: ResourceIdentity
    identity_revision: int | None = None
    identity_source: str | None = None
    name: str
    bangumi_id: int
    series_name: str = ""
    created_at: str = ""
    updated_at: str = ""
    download_path: str = ""
    active: int = 1
    primary: RssSource = RssSource()
    backup: RssSource = RssSource()
    bgm: BgmMeta = BgmMeta()
    tvdb: TvdbMeta = TvdbMeta()
    tmdb: TmdbMeta = TmdbMeta()
    poster_url: str = ""
    downloaded_count: int = 0
    primary_max_episode: int = 0
    backup_max_episode: int = 0


class ScanStatus(BaseModel):
    running: bool
    dir: str
    total: int
    processed: int
    deleted: int
    failed: int
    current_file: str
    errors: list[str]


class TmdbSearchResult(BaseModel):
    id: int
    name: str
    original_name: str = ""
    first_air_date: str = ""
    poster_path: str = ""


class SetTmdbRequest(BaseModel):
    tmdb_id: int
    tmdb_season: int | None = None


class MappingPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title_cn: str | None = None
    title_jp: str | None = None
    mikan_id: int | None = None
    tmdb_id: int | None = None
    tvdb_id: int | None = None
    tmdb_season: int | None = None
    tvdb_season: int | None = None
    tmdb_series_title_cn: str | None = None
    tmdb_series_title_jp: str | None = None
    tvdb_series_title_cn: str | None = None
    tvdb_series_title_jp: str | None = None
    bangumi_subject_title_cn: str | None = None
    bangumi_subject_title_jp: str | None = None
    # 平台季内集数 = Bangumi sort + ep_offset；显式 null 用于清除，0 为已确认相同。
    tmdb_ep_offset: int | None = None
    tvdb_ep_offset: int | None = None
