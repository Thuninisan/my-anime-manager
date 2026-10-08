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
  primary_max_episode?: number;
  backup_max_episode?: number;
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
  PREVIEW_SESSION_TTL_HOURS: number;
  RSS_EXCLUDE_PATTERNS: string[];
  RSS_POLL_INTERVAL_MIN: number;
  RESOURCE_POLL_INTERVAL_MIN: number;
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
  MOVIE_HARDLINK_PATH: string;
  FONTINASS_ENABLED: boolean;
  FONTINASS_URL: string;
  FONTINASS_TIMEOUT: number;
  RSS_PATH_TEMPLATE: string;
}

/** Normalized preview used by the torrent UI; raw wire payloads stop in adapters. */
export interface TorrentPreviewResponse {
  series?: { show_key: string; display_name: string; tmdb_series_id: number | null;
    tvdb_series_id: number | null; bangumi_subject_id: number | null }[];
  preview_id: string;
  revision: number;
  expires_at: string;
  subtitle_files?: import('./matchTable').ParsedFile[];
  parsed_files: import('./matchTable').ParsedFile[];
  search_results: Record<string, import('./matchTable').SearchEntry>;
  episode_data: import('./episode').EpisodeCatalog;
  index?: 'tmdb' | 'tvdb';
  specials?: import('./matchTable').ParsedFile[];
  skipped_files?: { file_name: string; reason?: string; torrent_path?: string }[];
  subtitles?: string[]; torrent_name: string; torrent_path: string;
  error?: string;
  resource_id?: number;
  preprocessed_candidates?: { bangumi_id: number; index_season: number; media_type: string; reason: string }[];
}
