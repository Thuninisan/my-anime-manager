/** Shared types for the MatchTable matching pipeline.
 *
 * These were extracted from MatchTable.tsx to break the circular type
 * dependency between MatchTable and MappingCard, and to keep type
 * definitions separate from component logic.
 */

export interface ParsedFile {
  file_name: string;
  torrent_path: string;
  show_name: string;
  season: number;
  episode: number;
}

export interface SearchEntry {
  tmdb: { id: number; name: string; original_title?: string; original_name?: string } | null;
  bangumi: { id: number; name: string; name_cn?: string } | null;
  media_type?: "tv" | "movie" | "special";
  map_entries?: { bangumi_id: number; name: string; tvdb_id?: number; tvdb_season?: number; tmdb_season?: number }[];
}

export interface TmdbEpisode {
  epNum: number;
  tmdbId: number;
  name: string;
  name_cn?: string;
}

export interface TmdbSeason {
  name: string;
  episodes: TmdbEpisode[];
}

export interface BgmEpisode {
  sort: number;
  id: number;
  name: string;
  name_cn?: string;
}

export interface BgmEntry {
  name: string;
  episodes: BgmEpisode[];
}

export interface MatchRow {
  file_name: string;
  torrent_path: string;
  show_name: string;
  src_season: number;
  src_episode: number;
  bgm_entry: string;
  bgm_entry_id: number | null;
  bgm_sort: number | null;
  bgm_ep_name: string;
  bgm_ep_name_cn: string;
  bgm_ep_id: number | null;
  tmdb_season: number | null;
  tmdb_ep: number | null;
  tmdb_ep_name: string;
  tvdb_season: number | null;
  tvdb_ep: number | null;
  matched: boolean;
  media_type?: "tv" | "movie" | "special";
}
