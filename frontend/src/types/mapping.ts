export interface MappingFields {
  mikan_id: number | null;
  tmdb_id: number | null;
  tmdb_season: number | null;
  tmdb_ep_offset: number | null;
  tvdb_id: number | null;
  tvdb_season: number | null;
  tvdb_ep_offset: number | null;
}

export interface BangumiMapping extends MappingFields {
  bangumi_id: number;
  override: boolean;
  tmdb_series_title_cn: string | null;
  tmdb_series_title_jp: string | null;
  tvdb_series_title_cn: string | null;
  tvdb_series_title_jp: string | null;
}

export type MappingUpdate = Partial<MappingFields>;

export interface MappingChangeTask {
  task_id: string;
  bangumi_id: number;
  kind: 'cleanup' | 'nfo';
  status: 'preview' | 'queued' | 'running' | 'completed' | 'partial_failed' | 'failed';
  message: string;
  plan?: {
    files: { path: string }[];
    torrents: { hash: string }[];
    warnings: string[];
    media_count: number;
    nfo_count: number;
  };
  old_mikan_id?: number | null;
  new_mikan_id?: number | null;
  episode_count?: number;
  total?: number;
  processed?: number;
  succeeded?: number;
  failed?: number;
  current_sort?: number | null;
  deletion_started?: boolean;
  thumbnails?: { updated: number; missing: number; failed: number };
  results: {
    path: string;
    status: 'success' | 'failed';
    message: string;
    sort?: number;
    thumbnail_status?: 'success' | 'missing' | 'failed' | 'not_attempted';
    thumbnail_path?: string;
  }[];
}

export interface MappingChangePreview {
  kind: 'cleanup' | 'nfo_offer' | 'simple';
  bangumi_id: number;
  episode_count: number;
  task_id?: string;
  old_mikan_id: number | null;
  new_mikan_id: number | null;
  plan?: MappingChangeTask['plan'];
}
