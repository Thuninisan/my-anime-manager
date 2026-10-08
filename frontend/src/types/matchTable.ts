import type { EpisodeMapping, CatalogEpisode, CatalogSeason, BangumiCatalogEpisode, BangumiCatalogEntry } from './episode';
/** Shared types for the MatchTable matching pipeline.
 *
 * These were extracted from MatchTable.tsx to break the circular type
 * dependency between MatchTable and MappingCard, and to keep type
 * definitions separate from component logic.
 */

export interface ParsedFile {
  file_id?: string;
  file_name: string;
  torrent_path: string;
  show_name: string;
  parsed: import("./episode").ParsedEpisodeRef;
}

export interface SearchEntry {
  resource_resolution: import("./episode").ResourceResolution;
  resource_identity: import('./episode').ResourceIdentity | null;
  display_name: string;
  bangumi_display_name: string;
  tmdb_series_id: number | null;
  tmdb_movie_id: number | null;
  bangumi_subject_id: number | null;
  bangumi_subject_ids: number[];
  tvdb_series_id: number | null;
  media_type: "tv" | "movie" | "special";
  mapping_hints: { bangumi_subject_id: number | null; name: string;
    tvdb_series_id: number | null; tvdb_season_number: number | null; tmdb_season_number: number | null }[];
}

export type TmdbEpisode = CatalogEpisode;
export type TmdbSeason = CatalogSeason;
export type BgmEpisode = BangumiCatalogEpisode;
export type BgmEntry = BangumiCatalogEntry;

export interface MatchRow {
  file_name: string; torrent_path: string; show_name: string;
  mapping: EpisodeMapping;
  bgm_entry: string; bgm_ep_name: string; bgm_ep_name_cn: string; tmdb_ep_name: string;
  matched: boolean; media_type?: "tv" | "movie" | "special";
}
