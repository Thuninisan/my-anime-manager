/** Provider-independent episode coordinates and metadata. */
export interface ParsedEpisodeRef {
  season_number: number | null;
  episode_number: number | null;
}

export interface BangumiEpisodeRef {
  subject_id: number | null;
  episode_id: number | null;
  /** Bangumi API ep. */
  episode_number: number | null;
  /** Bangumi API sort, independent of ep. */
  episode_absolute: number | null;
}

export interface TmdbEpisodeRef {
  series_id: number | null;
  episode_id: number | null;
  season_number: number | null;
  episode_number: number | null;
}

export interface TvdbEpisodeRef extends TmdbEpisodeRef {}

export type EpisodeMatchSource = 'tmdb' | 'tvdb';

export interface EpisodeMapping {
  parsed: ParsedEpisodeRef;
  bangumi: BangumiEpisodeRef;
  tmdb: TmdbEpisodeRef;
  tvdb: TvdbEpisodeRef;
  match_source: EpisodeMatchSource | null;
}

export type MetadataSource = 'bangumi' | 'tmdb' | 'tvdb' | 'translated';

export interface EpisodeMetadataSources {
  title: MetadataSource | null;
  plot: MetadataSource | null;
  thumbnail: MetadataSource | null;
  rating: MetadataSource | null;
}

export interface EpisodeMetadata {
  title: string;
  original_title: string | null;
  plot: string | null;
  air_date: string | null;
  runtime_minutes: number | null;
  rating: number | null;
  thumbnail_url: string | null;
  directors: string[];
  writers: string[];
  actors: string[];
  sources: EpisodeMetadataSources;
}

export interface ResolvedEpisode {
  mapping: EpisodeMapping;
  metadata: EpisodeMetadata;
}

export interface SeriesMetadata {
  title: string;
  original_title: string | null;
  plot: string | null;
  genres: string[];
  studios: string[];
  status: string | null;
  poster_url: string | null;
  fanart_url: string | null;
}

/** Candidates are separate from a particular file's resolved mapping. */
export interface CatalogEpisode extends TmdbEpisodeRef {
  episode_number: number;
  name: string;
  name_cn?: string;
  episode_absolute?: number | null;
}

export interface CatalogSeason {
  name: string;
  episodes: CatalogEpisode[];
}

export interface BangumiCatalogEpisode extends BangumiEpisodeRef {
  matching_absolute?: number | null;
  name: string;
  name_cn?: string;
  episode_id: number;
}

export interface BangumiCatalogEntry {
  name: string;
  episodes: BangumiCatalogEpisode[];
}

export interface EpisodeCatalog {
  tmdb_series_titles?: Record<string, string>;
  tmdb: Record<string, Record<string, CatalogSeason>>;
  bangumi: Record<string, BangumiCatalogEntry>;
  tvdb: Record<string, { name: string; seasons: Record<string, CatalogSeason> }>;
}

export interface ResourceIdentity {
  media_type: 'tv' | 'movie';
  canonical_title: string | null;
  bangumi_subject_id: number | null;
  tmdb_series_id: number | null;
  tmdb_movie_id: number | null;
  tvdb_series_id: number | null;
}

export interface ResourceCandidate {
  provider: 'bangumi' | 'tmdb' | 'tvdb';
  provider_id: number;
  media_type: 'tv' | 'movie' | 'special' | 'unknown';
  title: string | null;
  original_title: string | null;
  alternative_titles: string[];
  year: number | null;
  source: string;
}

export interface ResourceResolution {
  status: 'resolved' | 'ambiguous' | 'unresolved';
  identity: ResourceIdentity | null;
  candidates: ResourceCandidate[];
  reason: string;
}

export interface EpisodeMappingSnapshot {
  schema_version: number;
  resource_identity_schema_version: number;
  resource_identity: ResourceIdentity | null;
  identity_revision: number | null;
  source: string;
  episode_mapping: EpisodeMapping;
  processing_result: Record<string, string> | null;
}
