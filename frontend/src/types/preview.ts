/* TypeScript interfaces — {tvshow, seasons, episodes} format. */

export interface TmdbEpisodeData {
  name: string;
  overview: string;
  air_date: string;
  runtime: number;
  id: number;
  still_path: string;
  directors: string[];
  writers: string[];
  guest_stars: { name: string; character: string }[];
}

export interface EpisodeBlock {
  oldPath: string;
  newPath: string;
  tmdb_season: number;
  season_number: number;
  episode_number: number;
  bangumi_subject_name: string;
  bangumi_ep_id: number | null;
  tmdb: TmdbEpisodeData | null;
}

export interface SeasonBlock {
  bgm_id: number;
  bgm_title: string;
  bgm_original: string;
  bgm_plot: string;
  bgm_premiered: string;
  bgm_images: unknown;
  tmdb_season_name: string;
}

export interface TvshowBlock {
  title: string;
  original_title: string;
  plot: string;
  premiered: string;
  tmdb_id: number;
  genres: string[];
  studios: string[];
  rating: number;
  status: string;
}

export interface ExtraBlock {
  oldPath: string;
  newPath: string;
  type: string;
}

export interface TmdbSeasonData {
  name: string;
  episodes: Record<string, TmdbEpisodeData>;
}

export interface BangumiEpisodeData {
  sort: number;
  id: number;
  name: string;
}

export interface BangumiSeasonData {
  name: string;
  subject_id: number;
  episodes: BangumiEpisodeData[];
  kind?: string;  // "剧场版", "总集篇", "OVA", "番外篇", etc. — absent for regular TV seasons
}

export interface TorrentPreviewResponse {
  torrent_path: string;
  torrent_name: string;
  save_path: string;
  output_root: string;
  tvshow: TvshowBlock;
  seasons: Record<string, SeasonBlock>;
  episodes: Record<string, EpisodeBlock>;
  extras: ExtraBlock[];
  tmdb_data: Record<string, TmdbSeasonData>;
  bangumi_data: Record<string, BangumiSeasonData>;
}

// Keep for ConfirmAction
export interface ConfirmResponse {
  ok: boolean;
  nfoGenerated: number;
  imagesDownloaded: number;
  filesRenamed: number;
  showDirName: string;
  error: string;
}

/* RSS */
export interface RssSubtitleGroup {
  name: string;
  subgroup_id: number;
  rss_url: string;
}

export interface BangumiRssResponse {
  bangumi_id: number;
  name: string;
  mikan_id: number;
  global_rss: string;
  groups: RssSubtitleGroup[];
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
}

export interface RssSettings {
  exclude_patterns: string[];
}

export interface RssFeedResponse {
  title: string;
  items: RssFeedItem[];
}

export interface SubscriptionOut {
  name: string;
  rss_url: string;
  bangumi_id: number;
  subgroup_id: number;
  subgroup_name: string;
  filter_tags: string[];
  backup_rss_url: string;
  backup_subgroup_id: number;
  backup_subgroup_name: string;
  backup_filter_tags: string[];
  created_at: string;
  updated_at: string;
  download_path?: string;
  active?: number;
  // Pre-computed season metadata (from Bangumi chain)
  bgm_season?: number;
  bgm_sortrange?: number[];
  tmdb_id?: number;
  tmdb_season?: number | null;
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
}

/* App state machine */
export type AppState =
  | 'idle'
  | 'uploading'
  | 'preview'
  | 'confirming'
  | 'done'
  | 'error';
