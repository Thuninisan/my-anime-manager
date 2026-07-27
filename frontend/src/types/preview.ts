/* TypeScript interfaces — shared types for RSS flow, download history, and config. */

/* TMDB season/episode info for download history dropdowns */

export interface TmdbEpisodeInfo {
  epNum: number;
  name: string;
  tmdbId: number;
  overview: string;
  airDate: string;
  runtime: number;
  stillPath: string;
}

export interface SeasonInfo {
  name: string;
  episodes: TmdbEpisodeInfo[];
}

/* RSS */
export interface RssSubtitleGroup {
  name: string;
  subgroup_id: number;
  rss_url: string;
}

export interface BangumiMeta {
  air_date: string;
  eps: number;
  rating: number;
  rating_total: number;
  series_name: string;
  poster_url: string;
}

export interface BangumiRssResponse {
  bangumi_id: number;
  name: string;
  mikan_id: number;
  global_rss: string;
  groups: RssSubtitleGroup[];
}

export interface MikanSearchResult {
  mikan_id: number;
  title: string;
  url: string;
}

export interface ManualSubscribeIn {
  name: string;
  rss_url: string;
  bangumi_id: number;
  backup_rss_url?: string;
}

export interface RssDataStatus {
  exists: boolean;
  count: number;
}

export interface SubscriptionIn {
  name: string;
  rss_url: string;
  bangumi_id: number;
  subgroup_id: number;
  subgroup_name: string;
  filter_tags: string[];
  backup_rss_url?: string;
  backup_subgroup_id?: number;
  backup_subgroup_name?: string;
  backup_filter_tags?: string[];
  download_path?: string;
  exclude_patterns?: string[];
  backup_exclude_patterns?: string[];
}

export interface RssFeedItem {
  guid: string;
  title: string;
  torrent_url: string;
  pub_date: string;
  size_bytes: number;
  downloaded: boolean;
  tags: string[];
  passed: boolean;
  excluded: boolean;
  episode_number: number;
}

export interface RssSettings {
  exclude_patterns: string[];
}

export interface RssFeedResponse {
  title: string;
  items: RssFeedItem[];
}

export interface BgmMeta {
  season?: number;
  sortrange?: number[];
  subject_name?: string;
  rating?: number;
  air_date?: string;
}

export interface TvdbMeta {
  id?: number;
  season?: number | null;
  ep_offset?: number;
}

export interface TmdbMeta {
  id?: number;
  season?: number | null;
  ep_offset?: number;
}

export interface RssSourceMeta {
  rss_url: string;
  subgroup_id: number;
  subgroup_name: string;
  filter_tags: string[];
  exclude_patterns?: string[];
}

export interface SubscriptionOut {
  name: string;
  bangumi_id: number;
  series_name?: string;
  created_at: string;
  updated_at: string;
  download_path?: string;
  active?: number;
  primary: RssSourceMeta;
  backup: RssSourceMeta;
  bgm: BgmMeta;
  tvdb: TvdbMeta;
  tmdb: TmdbMeta;
  poster_url?: string;
  downloaded_count?: number;
}

/* Download history */
export interface QbitTorrentInfo {
  name: string;
  progress: number;
  state: string;
  size: number;
  dlspeed: number;
  eta: number;
  added_on: number;
  completion_on: number;
  save_path: string;
}

export interface EpisodeHistoryEntry {
  sort: number;
  source: string;
  guid: string;
  at: string;
  info_hash: string;
  tmdb_ep?: number | null;
  tmdb_season?: number | null;
  qbit: QbitTorrentInfo | null;
}

export interface DownloadHistoryResponse {
  bangumi_id: number;
  name: string;
  bgm_season: number;
  bgm_sortrange: number[];
  episodes: EpisodeHistoryEntry[];
  missing_sorts: number[];
}

/* Config */
export interface AppConfig {
  TMDB_API_KEY: string;
  TVDB_API_KEY: string;
  DEEPSEEK_API_KEY: string;
  BANGUMI_UA: string;
  API_DELAY_MS: number;
  PROXY_HOST: string;
  PROXY_PORT: number;
  TORRENT_WATCH_DIR: string;
  MIKAN_BASE_URL: string;
  QBITTORRENT_URL: string;
  QBITTORRENT_USERNAME: string;
  QBITTORRENT_PASSWORD: string;
  QBITTORRENT_SAVE_PATH: string;
  RSS_DOWNLOAD_PATH: string;
  TORRENT_DOWNLOAD_PATH: string;
  TORRENT_EXCLUDE_PATTERNS: string;
  TORRENT_HARDLINK_PATH: string;
  RSS_PATH_TEMPLATE: string;
}
