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
