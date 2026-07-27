"""API request/response Pydantic models."""

from pydantic import BaseModel

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
    rating: float = 0.0
    air_date: str = ""


class TvdbMeta(BaseModel):
    id: int = 0
    season: int | None = None
    ep_offset: int = 0


class TmdbMeta(BaseModel):
    id: int = 0
    season: int | None = None
    ep_offset: int = 0


class RssSource(BaseModel):
    rss_url: str = ""
    subgroup_id: int = 0
    subgroup_name: str = ""
    filter_tags: list[str] = []
    exclude_patterns: list[str] = []


class SubscriptionOut(BaseModel):
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


class IntervalBody(BaseModel):
    minutes: int
