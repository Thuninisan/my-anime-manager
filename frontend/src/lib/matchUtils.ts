import { automaticVideoFiles, candidateIds, recommendedId } from './episodeAdapters';
import type { EpisodeCatalog, CatalogSeason } from '@/types/episode';
import type { TorrentPreviewResponse } from '@/types/preview';
import { createEpisodeMapping, catalogSeriesTitle } from './episodeAdapters';
/** Pure matching utilities — extracted from MatchTable.tsx.
 *
 * All functions in this module have zero React dependencies and can be
 * tested independently.  They implement the core anime-episode matching
 * pipeline: parsed coordinates → selected TMDB/TVDB index → episode titles
 * in the other provider directories.
 */

import type {
  ParsedFile, SearchEntry, TmdbSeason,
  MatchRow,
} from '@/types/matchTable';
import type { TmdbSeasonOption, TmdbEpOption } from '@/components/torrent/MappingCard';

// ═══════════════════════════════════════════════════════════════════════
// Subtitle helpers
// ═══════════════════════════════════════════════════════════════════════

/** Allowed subtitle extensions for batch folder upload. */
export const BATCH_SUB_EXTENSIONS = new Set(['.ass', '.ssa', '.srt', '.sub', '.idx', '.vtt', '.ttml', '.sbv', '.dfxp']);

/** Extract a candidate episode number from a subtitle filename.
 *  Tries several common anime naming patterns; returns the number or null. */
export function extractEpisodeNumber(filename: string): number | null {
  const name = filename.replace(/\\/g, '/').split('/').pop() || filename;
  const patterns = [
    /[\[【\(（#](\d{1,3})(?:v\d+)?[\]】\)）]/,       // [01], (01), #01 etc.
    /[Ee](\d{1,3})(?:\s|$|[._-])/,                     // E01, e01
    /第\s*(\d{1,3})\s*[话話]/,                            // 第01话
    /[-_\.\s](\d{1,3})(?:v\d+)?(?:\.[^.]+)?$/,          // trailing -01 before ext
    /[-_\.\s](\d{1,3})(?:v\d+)?[-_\.\s]/,               // -01- or _01_ in the middle
  ];
  for (const re of patterns) {
    const m = name.match(re);
    if (m) {
      const n = parseInt(m[1], 10);
      if (n >= 1 && n <= 999) return n;
    }
  }
  return null;
}

// ═══════════════════════════════════════════════════════════════════════
// String normalisation + fuzzy matching
// ═══════════════════════════════════════════════════════════════════════

/**
 * Normalise a string for fuzzy comparison: full-width → half-width
 * for ASCII-range characters (e.g. "！" → "!", "＂" → "\""),
 * then trim and lowercase.
 */
export function normalise(s: string): string {
  return s
    .normalize("NFKC")         // Unicode canonical + compatibility composition
    .replace(/[！-～]/g, (ch) =>
      String.fromCharCode(ch.charCodeAt(0) - 0xFF01 + 0x21),
    )
    .replace(/　/g, " ")       // full-width space → half-width space
    .trim()
    .toLowerCase();
}

/** Character-level Dice coefficient in [0, 1].  Treats each string as a
 *  bag of characters (after normalisation).  Higher = more similar. */
export function charSimilarity(a: string, b: string): number {
  const sa = new Set(a);
  const sb = new Set(b);
  if (sa.size === 0 && sb.size === 0) return 1;
  let overlap = 0;
  for (const ch of sa) {
    if (sb.has(ch)) overlap++;
  }
  return (2 * overlap) / (sa.size + sb.size);
}

// ═══════════════════════════════════════════════════════════════════════
// Season merger helpers (TMDB + TVDB)
// ═══════════════════════════════════════════════════════════════════════

/** Merge seasons from every loaded TMDB entry into one flat map.
 *  Episodes from different entries that share the same season number are
 *  combined (deduplicated by episode_number).  Sentinel keys like `_name` are skipped. */
export function mergeAllTmdbSeasons(episodeData: EpisodeCatalog): Record<string, TmdbSeason> {
  const merged: Record<string, TmdbSeason> = {};
  for (const seasons of Object.values(episodeData.tmdb || {})) {
    for (const [skey, sdata] of Object.entries(seasons)) {
      if (!sdata?.episodes) continue;
      if (!merged[skey]) {
        merged[skey] = { name: sdata.name, episodes: [...sdata.episodes] };
      } else {
        const seen = new Set(merged[skey].episodes.map((e) => e.episode_number));
        for (const ep of sdata.episodes || []) {
          if (!seen.has(ep.episode_number)) merged[skey].episodes.push({ ...ep });
        }
      }
    }
  }
  return merged;
}


// ═══════════════════════════════════════════════════════════════════════
// Options-building helpers (used by MatchTable render sections)
// ═══════════════════════════════════════════════════════════════════════

/** Build TMDB season options for a single TV row. */
export function buildTmdbSeasonOptions(
  showName: string,
  searchResults: Record<string, SearchEntry>,
  episodeData: EpisodeCatalog,
): { seasons: Record<string, TmdbSeason>; opts: TmdbSeasonOption[] } {
  const ids = candidateIds(searchResults[showName], 'tmdb');
  const scoped = Object.fromEntries(Object.entries(episodeData.tmdb).filter(([id]) => ids.includes(Number(id))));
  const seasons = mergeAllTmdbSeasons({ ...episodeData, tmdb: scoped });
  const opts = Object.entries(scoped).flatMap(([id, seasons]) => Object.entries(seasons)
    .map(([season, detail]) => ({ value: `${id}:${season}`,
      label: `${searchResults[showName]?.candidates.tmdb.find(candidate => candidate.provider_id === Number(id))?.title || catalogSeriesTitle(episodeData, id) || `TMDB ${id}`} · ${detail.name || `Season ${season}`}` })));

  return { seasons, opts };
}

/** Build TMDB episode options for a given season. */
export function buildTmdbEpOptions(
  season: number | null,
  seasons: Record<string, TmdbSeason>,
): TmdbEpOption[] {
  const key = season != null ? String(season) : '';
  const seasonData = key ? seasons[key] : null;
  const seen = new Set<string>();
  const opts: TmdbEpOption[] = (seasonData?.episodes || []).filter(ep => {
    const identity = `${ep.series_id}:${ep.season_number}:${ep.episode_id ?? `number:${ep.episode_number}`}`;
    if (seen.has(identity)) return false;
    seen.add(identity);
    return true;
  }).sort((a, b) => a.episode_number - b.episode_number);
  return opts;
}

/** Build TVDB season options for a single TV row.
 *  Resolves the TVDB show ID from map entries matching the current BGM entry. */
export function buildTvdbSeasonOptions(
  currentEntryId: number,
  showName: string,
  searchResults: Record<string, SearchEntry>,
  episodeData: EpisodeCatalog,
  overrideTvdbShowId?: number,
): { seasons: Record<string, CatalogSeason>; opts: TmdbSeasonOption[]; tvdbShowId: number | undefined } {
  const entry = searchResults[showName];
  const mapEntries = entry?.mapping_hints || [];
  const mapEntry = mapEntries.find(me => me.bangumi_subject_id === currentEntryId);
  const tvdbData = episodeData?.tvdb || {};
  const allowed = entry ? new Set(candidateIds(entry, 'tvdb'))
    : new Set(Object.keys(tvdbData).map(Number));
  const scoped = Object.fromEntries(Object.entries(tvdbData).filter(([id]) => allowed.has(Number(id))));
  const onlyId = Object.keys(scoped).length === 1 ? Number(Object.keys(scoped)[0]) : undefined;
  const effectiveTvdbId = overrideTvdbShowId ?? recommendedId(entry, 'tvdb') ?? mapEntry?.tvdb_series_id ?? onlyId;
  const seasons: Record<string, CatalogSeason> = effectiveTvdbId != null && scoped[String(effectiveTvdbId)]
    ? scoped[String(effectiveTvdbId)].seasons : {};
  const opts = Object.entries(scoped).flatMap(([id, series]) => Object.entries(series.seasons)
    .filter(([, season]) => season.episodes)
    .map(([season, detail]) => ({ value: `${id}:${season}`,
      label: `${series.name || `TVDB ${id}`} · ${detail.name || `Season ${season}`}` })));

  return { seasons, opts, tvdbShowId: effectiveTvdbId };
}

/** Build TVDB episode options for a given season. */
export function buildTvdbEpOptions(
  season: number | null,
  seasons: Record<string, CatalogSeason>,
): { opts: TmdbEpOption[]; title: string } {
  const key = season != null ? String(season) : '';
  const seasonData = key ? seasons[key] : null;
  const seen = new Set<string>();
  const opts: TmdbEpOption[] = (seasonData?.episodes || []).filter(ep => {
    const identity = `${ep.series_id}:${ep.season_number}:${ep.episode_id ?? `number:${ep.episode_number}`}`;
    if (seen.has(identity)) return false;
    seen.add(identity);
    return true;
  }).sort((a, b) => a.episode_number - b.episode_number);
  const title = season == null ? '请先选择 TVDB 季' : opts.length === 0
    ? '该季暂无集数' : opts[0].name || '-';
  return { opts, title };
}

/** Build cross-show season options for SP rows (TMDB or TVDB source). */
export function buildSpSeasonOptions(
  episodeData: EpisodeCatalog,
  searchResults: Record<string, SearchEntry>,
  source: 'tmdb' | 'tvdb',
): TmdbSeasonOption[] {
  const opts: TmdbSeasonOption[] = [];
  const dataMap = episodeData?.[source] || {};

  for (const idStr of Object.keys(dataMap)) {
    const showId = Number(idStr);
    if (!Object.values(searchResults).some(entry => candidateIds(entry, source).includes(showId))) continue;
    let showLabel = '';
    if (source === 'tmdb') {
      for (const [, entry] of Object.entries(searchResults)) {
        if (recommendedId(entry, 'tmdb') === showId) {
          showLabel = entry.display_name;
          break;
        }
      }
    }
    if (!showLabel) {
      showLabel = (source === 'tmdb' ? catalogSeriesTitle(episodeData, idStr) : episodeData.tvdb[idStr]?.name) || `${source.toUpperCase()} ${showId}`;
    }

    const seasons: Record<string, CatalogSeason> = source === 'tmdb'
      ? episodeData.tmdb[idStr] || {}
      : episodeData.tvdb[idStr]?.seasons || {};

    for (const [skey, sdata] of Object.entries(seasons)) {
      if (!sdata?.episodes) continue;
      opts.push({
        value: `${showId}:${skey}`,
        label: `${showLabel}  ${sdata.name || `Season ${skey}`}`,
      });
    }
  }
  return opts;
}

// ═══════════════════════════════════════════════════════════════════════
// Index-based matching (TMDB-first / TVDB-first)
// ═══════════════════════════════════════════════════════════════════════

/** Error thrown when parsed_files contain duplicate (season, episode) pairs. */
export class DuplicateEpisodeError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "DuplicateEpisodeError";
  }
}

/** Duplicate episode coordinates are scoped to a resource, not the whole torrent. */
export function checkDuplicates(parsedFiles: ParsedFile[]): void {
  const seen = new Map<string, string>();
  for (const pf of parsedFiles) {
    const key = `${pf.show_name.toLocaleLowerCase()}:S${pf.parsed.season_number}E${pf.parsed.episode_number}`;
    const existing = seen.get(key);
    if (existing) {
      throw new DuplicateEpisodeError(
        `S${pf.parsed.season_number}E${pf.parsed.episode_number} 同时匹配到 "${pf.file_name}" 和 "${existing}"`,
      );
    }
    seen.set(key, pf.file_name);
  }
}


/**
 * TMDB-first matching:
 *   parsed_files S+E → TMDB direct → TMDB name → BGM + TVDB
 */
function isolateSeries(data: TorrentPreviewResponse, file: ParsedFile): TorrentPreviewResponse {
  const entry = data.search_results[file.show_name];
  const catalog = data.episode_catalog;
  return { ...data, parsed_files: [file], search_results: entry ? { [file.show_name]: entry } : {},
    episode_catalog: { ...catalog,
      tmdb: Object.fromEntries(Object.entries(catalog.tmdb ?? {}).filter(([id]) => candidateIds(entry, 'tmdb').includes(Number(id)))),
      bangumi: Object.fromEntries(Object.entries(catalog.bangumi ?? {}).filter(([id]) => candidateIds(entry, 'bangumi').includes(Number(id)))),
      tvdb: Object.fromEntries(Object.entries(catalog.tvdb ?? {}).filter(([id]) => candidateIds(entry, 'tvdb').includes(Number(id)))),
    } };

}

export type EpisodeMatchStatus = 'matched' | 'ambiguous' | 'missing';

/** Rank episode titles without resolving ties by directory order. */
export function matchEpisodeTitles<T extends { name: string; name_cn?: string }>(name: string, candidates: T[]): { status: EpisodeMatchStatus; selected: T | null; candidates: T[] } {
  const query = normalise(name);
  if (!query) return { status: 'missing', selected: null, candidates: [] };
  const ranked = candidates.map(candidate => ({ candidate,
    exact: [candidate.name, candidate.name_cn || ''].some(title => normalise(title) === query),
    score: Math.max(
    ...[candidate.name, candidate.name_cn || ''].map(title => {
      const normalized = normalise(title);
      return normalized === query ? 1 : normalized ? charSimilarity(query, normalized) : 0;
    })) })).filter(item => item.score >= 0.55).sort((a, b) => b.score - a.score);
  if (!ranked.length) return { status: 'missing', selected: null, candidates: [] };
  const exact = ranked.filter(item => item.exact);
  const contenders = exact.length ? exact : ranked.filter(item => ranked[0].score - item.score < 0.08);
  return contenders.length === 1
    ? { status: 'matched', selected: contenders[0].candidate, candidates: [contenders[0].candidate] }
    : { status: 'ambiguous', selected: null, candidates: contenders.map(item => item.candidate) };
}

function computeIndexedMatches(data: TorrentPreviewResponse, index: 'tmdb' | 'tvdb'): MatchRow[] {
  checkDuplicates(automaticVideoFiles(data.parsed_files || []));
  return automaticVideoFiles(data.parsed_files || []).map(pf => {
    const scoped = isolateSeries(data, pf);
    const entry = scoped.search_results[pf.show_name];
    const catalog = scoped.episode_catalog;
    if (entry?.media_type === 'movie') {
      return { mapping: createEpisodeMapping(pf.parsed, recommendedId(entry, 'bangumi'), null, null, null, index),
        file_name: pf.file_name, torrent_path: pf.torrent_path, show_name: pf.show_name,
        bgm_entry: entry.candidates.bangumi[0]?.title || entry.bangumi_display_name || '-', bgm_ep_name: entry.candidates.bangumi[0]?.title || entry.bangumi_display_name || '-',
        bgm_ep_name_cn: '', tmdb_ep_name: entry.candidates.tmdb[0]?.title || entry.display_name || '-',
        tmdb_movie_id: recommendedId(entry, 'tmdb') ?? undefined, matched: !!(recommendedId(entry, 'tmdb') && recommendedId(entry, 'bangumi')), media_type: 'movie' };
    }
    const tmdbEpisodes = Object.values(catalog.tmdb || {}).flatMap(seasons =>
      Object.values(seasons).flatMap(season => season.episodes || []));
    const tvdbEpisodes = Object.values(catalog.tvdb || {}).flatMap(series =>
      Object.values(series.seasons).flatMap(season => season.episodes || []));
    const coordinates = (index === 'tmdb' ? tmdbEpisodes : tvdbEpisodes).filter(ep =>
      ep.season_number === pf.parsed.season_number && ep.episode_number === pf.parsed.episode_number);
    const anchor = coordinates.length === 1 ? coordinates[0] : null;
    const other = matchEpisodeTitles(anchor?.name || '', index === 'tmdb' ? tvdbEpisodes : tmdbEpisodes);
    const bgmEpisodes = Object.entries(catalog.bangumi || {}).flatMap(([id, subject]) =>
      subject.episodes.map(ep => ({ ...ep, subject_id: Number(id), subject_name: subject.name })));
    const bgm = matchEpisodeTitles(anchor?.name || '', bgmEpisodes);
    const tmdb = index === 'tmdb' ? anchor : other.selected;
    const tvdb = index === 'tvdb' ? anchor : other.selected;
    const statuses = { [index]: coordinates.length === 1 ? 'matched' : coordinates.length > 1 ? 'ambiguous' : 'missing',
      [index === 'tmdb' ? 'tvdb' : 'tmdb']: other.status, bangumi: bgm.status } as Record<string, EpisodeMatchStatus>;
    return { mapping: createEpisodeMapping(pf.parsed, bgm.selected?.subject_id ?? null,
        bgm.selected, tmdb, tvdb, index),
      file_name: pf.file_name, torrent_path: pf.torrent_path, show_name: pf.show_name,
      bgm_entry: bgm.selected?.subject_name || '-', bgm_ep_name: bgm.selected?.name || '-',
      bgm_ep_name_cn: bgm.selected?.name_cn || '', tmdb_ep_name: tmdb?.name || '-',
      matched: anchor !== null && bgm.selected !== null,
      match_status: statuses,
      match_candidates: { [index]: coordinates, [index === 'tmdb' ? 'tvdb' : 'tmdb']: other.candidates,
        bangumi: bgm.candidates }, media_type: 'tv' };
  });
}

export function computeMatchesTmdb(data: TorrentPreviewResponse): MatchRow[] {
  return computeIndexedMatches(data, 'tmdb');
}
export function computeMatchesTvdb(data: TorrentPreviewResponse): MatchRow[] {
  return computeIndexedMatches(data, 'tvdb');
}

/** 按预览中明确指定的来源匹配，不兼容缺失匹配源的旧预览。 */
export function computeMatches(data: TorrentPreviewResponse): MatchRow[] {
  switch (data.episode_match_source) {
    case 'tmdb':
      return computeMatchesTmdb(data);
    case 'tvdb':
      return computeMatchesTvdb(data);
    default:
      throw new Error('预览缺少有效的集数匹配源，请重新解析种子');
  }
}
