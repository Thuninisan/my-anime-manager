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
  show_key: string;
  candidates: Record<'tmdb' | 'bangumi' | 'tvdb', import('./episode').ResourceCandidate[]>;
  display_name: string;
  bangumi_display_name: string;
  media_type: "tv" | "movie" | "special";
  mapping_hints: { bangumi_subject_id: number | null; name: string;
    tvdb_series_id: number | null; tvdb_season_number: number | null; tmdb_season_number: number | null }[];
}

export type TmdbEpisode = CatalogEpisode;
export type TmdbSeason = CatalogSeason;
export type BgmEpisode = BangumiCatalogEpisode;
export type BgmEntry = BangumiCatalogEntry;

export interface MatchRow {
  match_status?: Record<string, "matched" | "ambiguous" | "missing" | "manual">;
  match_candidates?: Record<string, (CatalogEpisode | BangumiCatalogEpisode)[]>;
  file_name: string; torrent_path: string; show_name: string;
  mapping: EpisodeMapping;
  tmdb_movie_id?: number;
  bgm_entry: string; bgm_ep_name: string; bgm_ep_name_cn: string; tmdb_ep_name: string;
  matched: boolean; media_type?: "tv" | "movie" | "special";
}
