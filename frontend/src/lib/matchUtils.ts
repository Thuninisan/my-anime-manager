import { automaticVideoFiles, candidateIds, recommendedId } from './episodeAdapters';
import type { EpisodeCatalog, CatalogSeason } from '@/types/episode';
import type { TorrentPreviewResponse } from '@/types/preview';
import { createEpisodeMapping, matchingBangumiAbsolute, catalogSeriesTitle } from './episodeAdapters';
/** Pure matching utilities — extracted from MatchTable.tsx.
 *
 * All functions in this module have zero React dependencies and can be
 * tested independently.  They implement the core anime-episode matching
 * pipeline: parsed coordinates → selected TMDB/TVDB index → episode titles
 * in the other provider directories.
 */

import type {
  ParsedFile, SearchEntry, TmdbEpisode, TmdbSeason,
  BgmEpisode, BgmEntry, MatchRow,
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

export function fuzzyMatchTmdb(
  bgmName: string,
  bgmNameCn: string,
  tmdbSeasons: Record<string, TmdbSeason>,
): { season: number; episode_number: number; name: string; reference: TmdbEpisode; score?: number } | null {
  const bgmNorm = normalise(bgmName);
  const bgmCnNorm = normalise(bgmNameCn);

  // Build flat candidate list
  const allEps: { season: number; ep: TmdbEpisode }[] = [];
  for (const [skey, sdata] of Object.entries(tmdbSeasons)) {
    for (const ep of sdata.episodes) {
      allEps.push({ season: Number(skey), ep });
    }
  }

  // Round 1: exact match (name or name_cn)
  for (const { season, ep } of allEps) {
    const names = [ep.name];
    if (ep.name_cn) names.push(ep.name_cn);
    for (const n of names) {
      const nn = normalise(n);
      if (nn === bgmNorm || (bgmCnNorm && nn === bgmCnNorm)) {
        return { season, episode_number: ep.episode_number, name: ep.name, reference: ep };
      }
    }
  }

  // Round 2 & 3 combined: collect substring matches + Dice similarity
  // No short-circuit — always compare both and pick the highest-scoring match.
  const MIN_SIMILARITY = 0.55;
  let bestSubstr: { season: number; episode_number: number; name: string; reference: TmdbEpisode; score: number } | null = null;
  let bestDice: { season: number; episode_number: number; name: string; reference: TmdbEpisode; score: number } | null = null;
  for (const { season, ep } of allEps) {
    const names = [ep.name];
    if (ep.name_cn) names.push(ep.name_cn);
    for (const n of names) {
      const nn = normalise(n);
      const scoreA = charSimilarity(bgmNorm, nn);
      const scoreB = bgmCnNorm ? charSimilarity(bgmCnNorm, nn) : 0;
      const score = Math.max(scoreA, scoreB);

      // Track best Dice candidate regardless
      if (score > (bestDice?.score ?? 0)) {
        bestDice = { season, episode_number: ep.episode_number, name: ep.name, reference: ep, score };
      }

      // Also track best substring candidate (with same Dice score for fair comparison)
      if ((nn && bgmNorm && (nn.includes(bgmNorm) || bgmNorm.includes(nn))) ||
          (nn && bgmCnNorm && (nn.includes(bgmCnNorm) || bgmCnNorm.includes(nn)))) {
        if (score > (bestSubstr?.score ?? -1)) {
          bestSubstr = { season, episode_number: ep.episode_number, name: ep.name, reference: ep, score };
        }
      }
    }
  }

  // Decision: prefer substring when its Dice score is >= the best pure-Dice candidate.
  // Otherwise fall back to pure Dice if it meets the threshold.
  // This handles cases like "解答篇" vs "解答編" — one-char difference has higher
  // Dice than a shorter substring match, so the non-substring candidate wins.
  if (bestSubstr && (!bestDice || bestSubstr.score >= bestDice.score)) {
    return { season: bestSubstr.season, episode_number: bestSubstr.episode_number, name: bestSubstr.name, reference: bestSubstr.reference };
  }
  if (bestDice && bestDice.score >= MIN_SIMILARITY) {
    return { season: bestDice.season, episode_number: bestDice.episode_number, name: bestDice.name, reference: bestDice.reference };
  }

  return null;
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

/** Merge seasons from every loaded TVDB entry into one flat map.
 *  TVDB analog of ``mergeAllTmdbSeasons`` — deduplicates per season and
 *  per episode_number.  Used as fallback when no specific TVDB show ID is resolved. */
export function mergeAllTvdbSeasons(tvdbData: EpisodeCatalog["tvdb"]): Record<string, CatalogSeason> {
  const merged: Record<string, CatalogSeason> = {};
  for (const [, seriesData] of Object.entries(tvdbData)) {
    const seasons = seriesData?.seasons || {};
    for (const [skey, sdata] of Object.entries(seasons)) {
      if (!sdata?.episodes) continue;
      if (!merged[skey]) {
        merged[skey] = { ...sdata, episodes: [...sdata.episodes] };
      } else {
        const seen = new Set(merged[skey].episodes.map((e) => e.episode_number));
        for (const ep of sdata.episodes || []) {
          if (!seen.has(ep.episode_number)) {
            merged[skey].episodes.push({ ...ep });
            seen.add(ep.episode_number);
          }
        }
      }
    }
  }
  return merged;
}

/** Flatten episodes from a merged seasons map into sorted TmdbEpOption[].
 *  Used when no specific season is selected — shows all episodes as a
 *  single flat list so the user can manually pick. */
export function buildFlattenedEpisodes(
  seasons: Record<string, CatalogSeason>,
): TmdbEpOption[] {
  const result: TmdbEpOption[] = [];
  for (const sdata of Object.values(seasons)) {
    if (sdata?.episodes) {
      for (const ep of sdata.episodes) {
        result.push(ep);
      }
    }
  }
  result.sort((a, b) => a.episode_number - b.episode_number);
  return result;
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

// Original Bangumi-first matching → _computeMatchesLegacy below
// Dispatch entry point → computeMatches below

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
 * Reverse fuzzy match: given a TMDB or TVDB episode name, find the
 * corresponding Bangumi entry + episode across all loaded BGM entries.
 * Three rounds: exact → contains → Dice similarity.
 */
export function fuzzyMatchBgm(
  sourceEpName: string,
  allBgmEntries: [string, BgmEntry][],
): { bgmId: number; bgmEntryName: string; bgmEp: BgmEpisode } | null {
  const sourceNorm = normalise(sourceEpName);
  if (!sourceNorm) return null;

  // Flatten all BGM episodes
  const flat: { bgmId: number; bgmEntryName: string; ep: BgmEpisode; nameNorm: string; nameCnNorm: string }[] = [];
  for (const [bidStr, entry] of allBgmEntries) {
    for (const ep of entry.episodes || []) {
      flat.push({
        bgmId: Number(bidStr),
        bgmEntryName: entry.name,
        ep,
        nameNorm: normalise(ep.name),
        nameCnNorm: normalise(ep.name_cn || ""),
      });
    }
  }

  // Round 1: exact match
  for (const item of flat) {
    if (item.nameNorm === sourceNorm || (item.nameCnNorm && item.nameCnNorm === sourceNorm)) {
      return { bgmId: item.bgmId, bgmEntryName: item.bgmEntryName, bgmEp: item.ep };
    }
  }

  // Round 2 & 3 combined: collect substring matches + Dice similarity
  // No short-circuit — always compare both and pick the highest-scoring match.
  const MIN_SIMILARITY = 0.55;
  let bestSubstr: { bgmId: number; bgmEntryName: string; bgmEp: BgmEpisode; score: number } | null = null;
  let bestDice: { bgmId: number; bgmEntryName: string; bgmEp: BgmEpisode; score: number } | null = null;
  for (const item of flat) {
    const scoreA = charSimilarity(sourceNorm, item.nameNorm);
    const scoreB = item.nameCnNorm ? charSimilarity(sourceNorm, item.nameCnNorm) : 0;
    const score = Math.max(scoreA, scoreB);

    // Track best Dice candidate regardless
    if (score > (bestDice?.score ?? 0)) {
      bestDice = { bgmId: item.bgmId, bgmEntryName: item.bgmEntryName, bgmEp: item.ep, score };
    }

    // Also track best substring candidate
    if ((item.nameNorm && sourceNorm && (item.nameNorm.includes(sourceNorm) || sourceNorm.includes(item.nameNorm))) ||
        (item.nameCnNorm && sourceNorm && (item.nameCnNorm.includes(sourceNorm) || sourceNorm.includes(item.nameCnNorm)))) {
      if (score > (bestSubstr?.score ?? -1)) {
        bestSubstr = { bgmId: item.bgmId, bgmEntryName: item.bgmEntryName, bgmEp: item.ep, score };
      }
    }
  }

  // Decision: prefer substring when its Dice score is >= the best pure-Dice candidate.
  if (bestSubstr && (!bestDice || bestSubstr.score >= bestDice.score)) {
    return { bgmId: bestSubstr.bgmId, bgmEntryName: bestSubstr.bgmEntryName, bgmEp: bestSubstr.bgmEp };
  }
  if (bestDice && bestDice.score >= MIN_SIMILARITY) {
    return { bgmId: bestDice.bgmId, bgmEntryName: bestDice.bgmEntryName, bgmEp: bestDice.bgmEp };
  }

  return null;
}

/**
 * Fuzzy match a TMDB episode name against ALL TVDB entries (all shows, all seasons).
 * Returns the first match in three rounds: exact → contains → Dice similarity.
 */
export function fuzzyMatchTvdb(
  sourceEpName: string,
  tvdbData: EpisodeCatalog["tvdb"],
): { tvdbSeason: number; tvdbEp: number; reference: TmdbEpisode } | null {
  const sourceNorm = normalise(sourceEpName);
  if (!sourceNorm) return null;

  // Flatten all TVDB episodes
  const flat: { tvdbSeason: number; tvdbEp: number; reference: TmdbEpisode; nameNorm: string }[] = [];
  for (const [, seriesData] of Object.entries(tvdbData)) {
    const seasons = seriesData?.seasons || {};
    for (const [skey, sdata] of Object.entries(seasons)) {
      for (const ep of sdata?.episodes || []) {
        flat.push({
          tvdbSeason: Number(skey),
          tvdbEp: ep.episode_number,
          reference: ep,
          nameNorm: normalise(ep.name || ""),
        });
      }
    }
  }

  // Round 1: exact match
  for (const item of flat) {
    if (item.nameNorm === sourceNorm) {
      return { tvdbSeason: item.tvdbSeason, tvdbEp: item.tvdbEp, reference: item.reference };
    }
  }

  // Round 2 & 3 combined: collect substring matches + Dice similarity
  // No short-circuit — always compare both and pick the highest-scoring match.
  const MIN_SIMILARITY = 0.55;
  let bestSubstr: { tvdbSeason: number; tvdbEp: number; reference: TmdbEpisode; score: number } | null = null;
  let bestDice: { tvdbSeason: number; tvdbEp: number; reference: TmdbEpisode; score: number } | null = null;
  for (const item of flat) {
    const score = charSimilarity(sourceNorm, item.nameNorm);

    // Track best Dice candidate regardless
    if (score > (bestDice?.score ?? 0)) {
      bestDice = { tvdbSeason: item.tvdbSeason, tvdbEp: item.tvdbEp, reference: item.reference, score };
    }

    // Also track best substring candidate
    if (item.nameNorm && (item.nameNorm.includes(sourceNorm) || sourceNorm.includes(item.nameNorm))) {
      if (score > (bestSubstr?.score ?? -1)) {
        bestSubstr = { tvdbSeason: item.tvdbSeason, tvdbEp: item.tvdbEp, reference: item.reference, score };
      }
    }
  }

  // Decision: prefer substring when its Dice score is >= the best pure-Dice candidate.
  if (bestSubstr && (!bestDice || bestSubstr.score >= bestDice.score)) {
    return { tvdbSeason: bestSubstr.tvdbSeason, tvdbEp: bestSubstr.tvdbEp, reference: bestSubstr.reference };
  }
  if (bestDice && bestDice.score >= MIN_SIMILARITY) {
    return { tvdbSeason: bestDice.tvdbSeason, tvdbEp: bestDice.tvdbEp, reference: bestDice.reference };
  }

  return null;
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

/** Dispatch entry point: selects matching strategy based on data.episode_match_source. */
export function computeMatches(data: TorrentPreviewResponse): MatchRow[] {
  if (data.episode_match_source === "tmdb") return computeMatchesTmdb(data);
  if (data.episode_match_source === "tvdb") return computeMatchesTvdb(data);
  // Legacy: no index field — use original Bangumi-first logic
  return computeMatchesLegacy(data);
}

/** Original Bangumi-first matching (legacy, used when data.episode_match_source is absent). */
/** Original Bangumi-first strategy for previews without an index. */
export function computeMatchesLegacy(data: TorrentPreviewResponse): MatchRow[] {
  const parsedFiles: ParsedFile[] = automaticVideoFiles(data.parsed_files || []);
  const searchResults: Record<string, SearchEntry> = data.search_results || {};
  if (Object.keys(searchResults).length > 1) {
    checkDuplicates(parsedFiles);
    return parsedFiles.flatMap(file => computeMatchesLegacy(isolateSeries(data, file)));
  }
  const episodeData = data.episode_catalog || { tmdb: {}, bangumi: {} };

  return parsedFiles.map((pf) => {
    const searchEntry = searchResults[pf.show_name];

    const tmdbSeriesId = recommendedId(searchEntry, 'tmdb');
    const autoSeasons: Record<string, TmdbSeason> =
      (tmdbSeriesId && episodeData.tmdb?.[String(tmdbSeriesId)]) || {};
    const tmdbSeasons: Record<string, TmdbSeason> =
      Object.keys(autoSeasons).length > 0
        ? autoSeasons
        : mergeAllTmdbSeasons(episodeData);

    const allBgmEntries = Object.entries(episodeData.bangumi || {});
    let bgmEp: BgmEpisode | null = null;
    let matchedBgmName = "";
    let matchedBgmId: number | null = null;
    let tmdbMatch: { season: number; episode_number: number; name: string; reference: TmdbEpisode } | null = null;

    if (searchEntry?.media_type === "movie") {
      const matched = !!(recommendedId(searchEntry, 'tmdb') && recommendedId(searchEntry, 'bangumi'));
      return {
        mapping: createEpisodeMapping(pf.parsed, recommendedId(searchEntry, 'bangumi') ?? null, null, null, null, data.episode_match_source ?? null),
        file_name: pf.file_name, torrent_path: pf.torrent_path, show_name: pf.show_name,
        bgm_entry: searchEntry.bangumi_display_name || (recommendedId(searchEntry, 'bangumi') ? `ID ${recommendedId(searchEntry, 'bangumi')}` : '-'),
        bgm_ep_name: searchEntry.bangumi_display_name || '-',
        bgm_ep_name_cn: searchEntry.bangumi_display_name || '', tmdb_ep_name: searchEntry.display_name || '-',
        tmdb_movie_id: recommendedId(searchEntry, 'tmdb') ?? undefined, matched, media_type: "movie",
      };
    }

    if (pf.parsed.season_number === 0) {
      const tmdbS0 = tmdbSeasons["0"];
      if (tmdbS0) {
        const tmdbEp = tmdbS0.episodes.find((ep) => ep.episode_number === pf.parsed.episode_number);
        if (tmdbEp) {
          tmdbMatch = { season: 0, episode_number: tmdbEp.episode_number, name: tmdbEp.name, reference: tmdbEp };
          const tmdbNorm = normalise(tmdbEp.name);
          for (const [bidStr, entry] of allBgmEntries) {
            const eps = entry.episodes || [];
            const found = eps.find((ep) => normalise(ep.name) === tmdbNorm);
            if (found) { bgmEp = found; matchedBgmName = entry.name; matchedBgmId = Number(bidStr); break; }
          }
        }
      }
    }

    if (!bgmEp) {
      const preferredBgmId = recommendedId(searchEntry, 'bangumi');
      const preferredEntry: BgmEntry | undefined =
        (preferredBgmId != null && episodeData.bangumi?.[String(preferredBgmId)]) || undefined;
      if (preferredEntry) {
        const found = (preferredEntry.episodes || []).find((ep) => matchingBangumiAbsolute(ep) === pf.parsed.episode_number) ?? null;
        if (found) { bgmEp = found; matchedBgmName = preferredEntry.name; matchedBgmId = preferredBgmId!; }
      }
      if (!bgmEp) {
        for (const [bidStr, entry] of allBgmEntries) {
          const found = (entry.episodes || []).find((ep) => matchingBangumiAbsolute(ep) === pf.parsed.episode_number) ?? null;
          if (found) { bgmEp = found; matchedBgmName = entry.name; matchedBgmId = Number(bidStr); break; }
        }
      }
      if (!bgmEp) {
        const primaryEntry: BgmEntry | undefined =
          (preferredBgmId && episodeData.bangumi?.[String(preferredBgmId)]) || undefined;
        const primaryEps = primaryEntry?.episodes || [];
        if (pf.parsed.episode_number != null && pf.parsed.episode_number > 0 && pf.parsed.episode_number <= primaryEps.length) {
          bgmEp = primaryEps[pf.parsed.episode_number - 1]; matchedBgmName = primaryEntry?.name || ""; matchedBgmId = preferredBgmId ?? null;
        }
      }
    }

    if (!tmdbMatch) {
      tmdbMatch = bgmEp?.name ? fuzzyMatchTmdb(bgmEp.name, bgmEp.name_cn || "", tmdbSeasons) : null;
    }

    let tvdbReference: TmdbEpisode | null = null;
    if (bgmEp && matchedBgmId != null) {
      const mapEntries = searchEntry?.mapping_hints || [];
      const mapEntry = mapEntries.find((me) => me.bangumi_subject_id === matchedBgmId);
      const tvdbSeriesId: number | undefined = recommendedId(searchEntry, 'tvdb') ?? mapEntry?.tvdb_series_id ?? undefined;
      if (tvdbSeriesId != null) {
        const tvdbSeries = episodeData?.tvdb?.[String(tvdbSeriesId)];
        const seasons: Record<string, CatalogSeason> = tvdbSeries?.seasons || {};
        for (const sdata of Object.values(seasons)) {
          const found = (sdata?.episodes || []).find((e) => e.episode_absolute === matchingBangumiAbsolute(bgmEp));
          if (found) { tvdbReference = found; break; }
        }
        if (tvdbReference == null && mapEntry?.tvdb_season_number != null) {
          const targetSeason = seasons[String(mapEntry.tvdb_season_number)];
          const found = (targetSeason?.episodes || []).find((e) => e.episode_number === matchingBangumiAbsolute(bgmEp));
          if (found) { tvdbReference = found; }
        }
      }
    }

    return {
      mapping: createEpisodeMapping(pf.parsed, matchedBgmId ?? recommendedId(searchEntry, 'bangumi') ?? null, bgmEp, tmdbMatch?.reference, tvdbReference, data.episode_match_source ?? null),
      file_name: pf.file_name, torrent_path: pf.torrent_path, show_name: pf.show_name,
      bgm_entry: matchedBgmName || (recommendedId(searchEntry, 'bangumi') ? `ID ${recommendedId(searchEntry, 'bangumi')}` : '-'),
      bgm_ep_name: bgmEp?.name || '-',
      bgm_ep_name_cn: bgmEp?.name_cn || '', tmdb_ep_name: tmdbMatch?.name || '-', matched: tmdbMatch !== null, media_type: "tv",
    };
  });
}
