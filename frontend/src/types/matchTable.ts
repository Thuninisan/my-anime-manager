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
  tmdb: { id: number; name: string; original_title?: string; original_name?: string } | null;
  bangumi: { id: number; name: string; name_cn?: string } | null;
  media_type?: "tv" | "movie" | "special";
  map_entries?: { bangumi_id: number; name: string; tvdb_id?: number; tvdb_season?: number; tmdb_season?: number }[];
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
