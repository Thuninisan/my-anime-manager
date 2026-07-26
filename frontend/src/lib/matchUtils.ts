/** Pure matching utilities — extracted from MatchTable.tsx.
 *
 * All functions in this module have zero React dependencies and can be
 * tested independently.  They implement the core anime-episode matching
 * pipeline: anitopy parse → Bangumi lookup → fuzzy TMDB mapping → TVDB
 * absolute-number mapping.
 */

import type {
  ParsedFile, SearchEntry, TmdbEpisode, TmdbSeason,
  BgmEpisode, BgmEntry, MatchRow,
} from '@/types/matchTable';
import type { TmdbSeasonOption, TmdbEpOption } from '@/components/Cards/MappingCard';

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
): { season: number; epNum: number; name: string; score?: number } | null {
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
        return { season, epNum: ep.epNum, name: ep.name };
      }
    }
  }

  // Round 2: contains/substring match
  for (const { season, ep } of allEps) {
    const names = [ep.name];
    if (ep.name_cn) names.push(ep.name_cn);
    for (const n of names) {
      const nn = normalise(n);
      if ((nn && bgmNorm && (nn.includes(bgmNorm) || bgmNorm.includes(nn))) ||
          (nn && bgmCnNorm && (nn.includes(bgmCnNorm) || bgmCnNorm.includes(nn)))) {
        return { season, epNum: ep.epNum, name: ep.name };
      }
    }
  }

  // Round 3: character-level Dice similarity (fallback for variant kanji)
  const MIN_SIMILARITY = 0.55;
  let best: { season: number; epNum: number; name: string; score: number } | null = null;
  for (const { season, ep } of allEps) {
    const names = [ep.name];
    if (ep.name_cn) names.push(ep.name_cn);
    for (const n of names) {
      const nn = normalise(n);
      const scoreA = charSimilarity(bgmNorm, nn);
      const scoreB = bgmCnNorm ? charSimilarity(bgmCnNorm, nn) : 0;
      const score = Math.max(scoreA, scoreB);
      if (score > (best?.score ?? 0)) {
        best = { season, epNum: ep.epNum, name: ep.name, score };
      }
    }
  }
  if (best && best.score >= MIN_SIMILARITY) {
    return best;
  }

  return null;
}

// ═══════════════════════════════════════════════════════════════════════
// Season merger helpers (TMDB + TVDB)
// ═══════════════════════════════════════════════════════════════════════

/** Merge seasons from every loaded TMDB entry into one flat map.
 *  Episodes from different entries that share the same season number are
 *  combined (deduplicated by epNum).  Sentinel keys like `_name` are skipped. */
export function mergeAllTmdbSeasons(episodeData: any): Record<string, TmdbSeason> {
  const merged: Record<string, TmdbSeason> = {};
  for (const seasons of Object.values(episodeData.tmdb || {})) {
    for (const [skey, sdata] of Object.entries(seasons as Record<string, any>)) {
      if (!sdata?.episodes) continue;
      if (!merged[skey]) {
        merged[skey] = { name: sdata.name, episodes: [...sdata.episodes] };
      } else {
        const seen = new Set(merged[skey].episodes.map((e: any) => e.epNum));
        for (const ep of sdata.episodes || []) {
          if (!seen.has(ep.epNum)) merged[skey].episodes.push({ ...ep });
        }
      }
    }
  }
  return merged;
}

/** Merge seasons from every loaded TVDB entry into one flat map.
 *  TVDB analog of ``mergeAllTmdbSeasons`` — deduplicates per season and
 *  per epNum.  Used as fallback when no specific TVDB show ID is resolved. */
export function mergeAllTvdbSeasons(tvdbData: Record<string, any>): Record<string, any> {
  const merged: Record<string, any> = {};
  for (const [, seriesData] of Object.entries(tvdbData)) {
    const seasons = seriesData?.seasons || {};
    for (const [skey, sdata] of Object.entries(seasons)) {
      if (!(sdata as any)?.episodes) continue;
      if (!merged[skey]) {
        merged[skey] = { ...(sdata as any), episodes: [...(sdata as any).episodes] };
      } else {
        const seen = new Set(merged[skey].episodes.map((e: any) => e.epNum));
        for (const ep of (sdata as any).episodes || []) {
          if (!seen.has(ep.epNum)) {
            merged[skey].episodes.push({ ...ep });
            seen.add(ep.epNum);
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
  seasons: Record<string, any>,
): TmdbEpOption[] {
  const result: TmdbEpOption[] = [];
  for (const sdata of Object.values(seasons)) {
    if ((sdata as any)?.episodes) {
      for (const ep of (sdata as any).episodes) {
        result.push(ep);
      }
    }
  }
  result.sort((a: any, b: any) => a.epNum - b.epNum);
  return result;
}

// ═══════════════════════════════════════════════════════════════════════
// Options-building helpers (used by MatchTable render sections)
// ═══════════════════════════════════════════════════════════════════════

/** Build TMDB season options for a single TV row. */
export function buildTmdbSeasonOptions(
  showName: string,
  searchResults: Record<string, SearchEntry>,
  episodeData: any,
): { seasons: Record<string, TmdbSeason>; opts: TmdbSeasonOption[] } {
  const tmdbId = searchResults[showName]?.tmdb?.id;
  const autoSeasons: Record<string, TmdbSeason> =
    (tmdbId && episodeData.tmdb?.[String(tmdbId)]) || {};
  const seasons: Record<string, TmdbSeason> =
    Object.keys(autoSeasons).length > 0
      ? autoSeasons
      : mergeAllTmdbSeasons(episodeData);
  const opts: TmdbSeasonOption[] = Object.entries(seasons)
    .filter(([, sdata]) => sdata?.episodes)
    .map(([skey, sdata]) => ({
      value: String(Number(skey)),
      label: sdata.name || `Season ${skey}`,
    }));
  return { seasons, opts };
}

/** Build TMDB episode options for a given season. */
export function buildTmdbEpOptions(
  season: number | null,
  seasons: Record<string, TmdbSeason>,
): TmdbEpOption[] {
  const key = season != null ? String(season) : '';
  const seasonData = key ? seasons[key] : null;
  let opts: TmdbEpOption[] = (seasonData?.episodes || [])
    .sort((a, b) => a.epNum - b.epNum);
  if (opts.length === 0) {
    opts = buildFlattenedEpisodes(seasons);
  }
  return opts;
}

/** Build TVDB season options for a single TV row.
 *  Resolves the TVDB show ID from map entries matching the current BGM entry. */
export function buildTvdbSeasonOptions(
  currentEntryId: number,
  showName: string,
  searchResults: Record<string, SearchEntry>,
  episodeData: any,
  overrideTvdbShowId?: number,
): { seasons: Record<string, any>; opts: TmdbSeasonOption[]; tvdbShowId: number | undefined } {
  const mapEntries: any[] = searchResults[showName]?.map_entries || [];
  const mapEntry = mapEntries.find((me: any) => me.bangumi_id === currentEntryId);
  const effectiveTvdbId: number | undefined = overrideTvdbShowId ?? mapEntry?.tvdb_id;
  const tvdbData = episodeData?.tvdb || {};

  let seasons: Record<string, any> = {};
  if (effectiveTvdbId != null && tvdbData[String(effectiveTvdbId)]) {
    seasons = tvdbData[String(effectiveTvdbId)].seasons || {};
  } else {
    seasons = mergeAllTvdbSeasons(tvdbData);
  }

  const opts: TmdbSeasonOption[] = Object.entries(seasons)
    .filter(([, sdata]) => (sdata as any)?.episodes)
    .map(([skey, sdata]) => ({
      value: String(Number(skey)),
      label: (sdata as any).name || `Season ${skey}`,
    }));

  return { seasons, opts, tvdbShowId: effectiveTvdbId };
}

/** Build TVDB episode options for a given season. */
export function buildTvdbEpOptions(
  season: number | null,
  seasons: Record<string, any>,
): { opts: TmdbEpOption[]; title: string } {
  const key = season != null ? String(season) : '';
  const seasonData = key ? seasons[key] : null;
  let opts: TmdbEpOption[] = (seasonData?.episodes || [])
    .sort((a: any, b: any) => a.epNum - b.epNum);
  if (opts.length === 0) {
    opts = buildFlattenedEpisodes(seasons);
  }
  const title = seasonData?.episodes?.[0]?.name || '-';
  return { opts, title };
}

/** Build cross-show season options for SP rows (TMDB or TVDB source). */
export function buildSpSeasonOptions(
  episodeData: any,
  searchResults: Record<string, SearchEntry>,
  source: 'tmdb' | 'tvdb',
): TmdbSeasonOption[] {
  const opts: TmdbSeasonOption[] = [];
  const dataMap: Record<string, any> = episodeData?.[source] || {};

  for (const [idStr, entryData] of Object.entries(dataMap)) {
    const showId = Number(idStr);
    let showLabel = '';
    if (source === 'tmdb') {
      for (const [, entry] of Object.entries(searchResults)) {
        if (entry.tmdb?.id === showId) {
          showLabel = entry.tmdb.name;
          break;
        }
      }
    }
    if (!showLabel) {
      showLabel = (entryData as any).name || `${source.toUpperCase()} ${showId}`;
    }

    const seasons = source === 'tmdb'
      ? (entryData as Record<string, any>)
      : (entryData as any)?.seasons || {};

    for (const [skey, sdata] of Object.entries(seasons)) {
      if (!(sdata as any)?.episodes) continue;
      opts.push({
        value: `${showId}:${skey}`,
        label: `${showLabel}  ${(sdata as any).name || `Season ${skey}`}`,
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

/** Check for duplicate (season, episode) pairs across all parsed files. */
export function checkDuplicates(parsedFiles: ParsedFile[]): void {
  const seen = new Map<string, string>();
  for (const pf of parsedFiles) {
    const key = `S${pf.season}E${pf.episode}`;
    const existing = seen.get(key);
    if (existing) {
      throw new DuplicateEpisodeError(
        `S${pf.season}E${pf.episode} 同时匹配到 "${pf.file_name}" 和 "${existing}"`,
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

  // Round 2: contains/substring match
  for (const item of flat) {
    if ((item.nameNorm && sourceNorm && (item.nameNorm.includes(sourceNorm) || sourceNorm.includes(item.nameNorm))) ||
        (item.nameCnNorm && sourceNorm && (item.nameCnNorm.includes(sourceNorm) || sourceNorm.includes(item.nameCnNorm)))) {
      return { bgmId: item.bgmId, bgmEntryName: item.bgmEntryName, bgmEp: item.ep };
    }
  }

  // Round 3: Dice similarity
  const MIN_SIMILARITY = 0.55;
  let best: { bgmId: number; bgmEntryName: string; bgmEp: BgmEpisode; score: number } | null = null;
  for (const item of flat) {
    const scoreA = charSimilarity(sourceNorm, item.nameNorm);
    const scoreB = item.nameCnNorm ? charSimilarity(sourceNorm, item.nameCnNorm) : 0;
    const score = Math.max(scoreA, scoreB);
    if (score > (best?.score ?? 0)) {
      best = { bgmId: item.bgmId, bgmEntryName: item.bgmEntryName, bgmEp: item.ep, score };
    }
  }
  if (best && best.score >= MIN_SIMILARITY) {
    return { bgmId: best.bgmId, bgmEntryName: best.bgmEntryName, bgmEp: best.bgmEp };
  }

  return null;
}

/**
 * Fuzzy match a TMDB episode name against ALL TVDB entries (all shows, all seasons).
 * Returns the first match in three rounds: exact → contains → Dice similarity.
 */
export function fuzzyMatchTvdb(
  sourceEpName: string,
  tvdbData: Record<string, any>,
): { tvdbSeason: number; tvdbEp: number } | null {
  const sourceNorm = normalise(sourceEpName);
  if (!sourceNorm) return null;

  // Flatten all TVDB episodes
  const flat: { tvdbSeason: number; tvdbEp: number; nameNorm: string }[] = [];
  for (const [, seriesData] of Object.entries(tvdbData)) {
    const seasons = seriesData?.seasons || {};
    for (const [skey, sdata] of Object.entries(seasons)) {
      for (const ep of (sdata as any)?.episodes || []) {
        flat.push({
          tvdbSeason: Number(skey),
          tvdbEp: ep.epNum,
          nameNorm: normalise(ep.name || ""),
        });
      }
    }
  }

  // Round 1: exact match
  for (const item of flat) {
    if (item.nameNorm === sourceNorm) {
      return { tvdbSeason: item.tvdbSeason, tvdbEp: item.tvdbEp };
    }
  }

  // Round 2: contains/substring match
  for (const item of flat) {
    if (item.nameNorm && (item.nameNorm.includes(sourceNorm) || sourceNorm.includes(item.nameNorm))) {
      return { tvdbSeason: item.tvdbSeason, tvdbEp: item.tvdbEp };
    }
  }

  // Round 3: Dice similarity
  const MIN_SIMILARITY = 0.55;
  let best: { tvdbSeason: number; tvdbEp: number; score: number } | null = null;
  for (const item of flat) {
    const score = charSimilarity(sourceNorm, item.nameNorm);
    if (score > (best?.score ?? 0)) {
      best = { tvdbSeason: item.tvdbSeason, tvdbEp: item.tvdbEp, score };
    }
  }
  if (best && best.score >= MIN_SIMILARITY) {
    return { tvdbSeason: best.tvdbSeason, tvdbEp: best.tvdbEp };
  }

  return null;
}

/**
 * TMDB-first matching:
 *   parsed_files S+E → TMDB direct → TMDB name → BGM + TVDB
 */
export function computeMatchesTmdb(data: any): MatchRow[] {
  const parsedFiles: ParsedFile[] = data.parsed_files || [];
  const searchResults: Record<string, SearchEntry> = data.search_results || {};
  const episodeData = data.episode_data || { tmdb: {}, bangumi: {}, tvdb: {} };

  // Global duplicate check
  checkDuplicates(parsedFiles);

  const allBgmEntries = Object.entries(episodeData.bangumi || {}) as [string, BgmEntry][];

  return parsedFiles.map((pf) => {
    const searchEntry = searchResults[pf.show_name];
    const tmdbId = searchEntry?.tmdb?.id;

    // ── Movie ──
    if (searchEntry?.media_type === "movie") {
      const matched = !!(searchEntry.tmdb && searchEntry.bangumi);
      return {
        file_name: pf.file_name,
        torrent_path: pf.torrent_path,
        show_name: pf.show_name,
        src_season: pf.season,
        src_episode: pf.episode,
        bgm_entry: searchEntry.bangumi?.name || (searchEntry.bangumi?.id ? `ID ${searchEntry.bangumi.id}` : '-'),
        bgm_entry_id: searchEntry.bangumi?.id ?? null,
        bgm_sort: null,
        bgm_ep_name: searchEntry.bangumi?.name || '-',
        bgm_ep_name_cn: searchEntry.bangumi?.name_cn || '',
        bgm_ep_id: null,
        tmdb_season: null,
        tmdb_ep: null,
        tmdb_ep_name: searchEntry.tmdb?.name || '-',
        tvdb_season: null,
        tvdb_ep: null,
        matched,
        media_type: "movie",
      };
    }

    // ── Direct TMDB match by season + episode ──
    const tmdbSeasons: Record<string, TmdbSeason> =
      (tmdbId && episodeData.tmdb?.[String(tmdbId)]) || {};
    const seasonData = tmdbSeasons[String(pf.season)];
    const tmdbEp = seasonData?.episodes?.find((e: TmdbEpisode) => e.epNum === pf.episode);
    const tmdbMatch = tmdbEp
      ? { season: pf.season, epNum: tmdbEp.epNum, name: tmdbEp.name }
      : null;

    const tmdbEpName = tmdbMatch?.name || '';

    // ── TMDB episode name → fuzzy match BGM ──
    const bgmMatch = tmdbEpName ? fuzzyMatchBgm(tmdbEpName, allBgmEntries) : null;

    // ── TMDB episode name → fuzzy match TVDB (all entries) ──
    const tvdbMatch = tmdbEpName ? fuzzyMatchTvdb(tmdbEpName, episodeData.tvdb || {}) : null;

    return {
      file_name: pf.file_name,
      torrent_path: pf.torrent_path,
      show_name: pf.show_name,
      src_season: pf.season,
      src_episode: pf.episode,
      bgm_entry: bgmMatch?.bgmEntryName || (searchEntry?.bangumi?.id ? `ID ${searchEntry.bangumi.id}` : '-'),
      bgm_entry_id: bgmMatch?.bgmId ?? searchEntry?.bangumi?.id ?? null,
      bgm_sort: bgmMatch?.bgmEp.sort ?? null,
      bgm_ep_name: bgmMatch?.bgmEp.name || '-',
      bgm_ep_name_cn: bgmMatch?.bgmEp.name_cn || '',
      bgm_ep_id: bgmMatch?.bgmEp.id ?? null,
      tmdb_season: tmdbMatch?.season ?? null,
      tmdb_ep: tmdbMatch?.epNum ?? null,
      tmdb_ep_name: tmdbEpName || '-',
      tvdb_season: tvdbMatch?.tvdbSeason ?? null,
      tvdb_ep: tvdbMatch?.tvdbEp ?? null,
      matched: tmdbMatch !== null,
      media_type: "tv",
    };
  });
}

/**
 * TVDB-first matching:
 *   parsed_files S+E → TVDB direct → TVDB name → TMDB → TMDB name → BGM
 */
export function computeMatchesTvdb(data: any): MatchRow[] {
  const parsedFiles: ParsedFile[] = data.parsed_files || [];
  const searchResults: Record<string, SearchEntry> = data.search_results || {};
  const episodeData = data.episode_data || { tmdb: {}, bangumi: {}, tvdb: {} };

  // Global duplicate check
  checkDuplicates(parsedFiles);

  const allBgmEntries = Object.entries(episodeData.bangumi || {}) as [string, BgmEntry][];
  // Merge all TMDB seasons for cross-show fuzzy matching
  const mergedTmdbSeasons = mergeAllTmdbSeasons(episodeData);
  const tvdbData = episodeData.tvdb || {};

  return parsedFiles.map((pf) => {
    const searchEntry = searchResults[pf.show_name];

    // ── Movie ──
    if (searchEntry?.media_type === "movie") {
      const matched = !!(searchEntry.tmdb && searchEntry.bangumi);
      return {
        file_name: pf.file_name,
        torrent_path: pf.torrent_path,
        show_name: pf.show_name,
        src_season: pf.season,
        src_episode: pf.episode,
        bgm_entry: searchEntry.bangumi?.name || (searchEntry.bangumi?.id ? `ID ${searchEntry.bangumi.id}` : '-'),
        bgm_entry_id: searchEntry.bangumi?.id ?? null,
        bgm_sort: null,
        bgm_ep_name: searchEntry.bangumi?.name || '-',
        bgm_ep_name_cn: searchEntry.bangumi?.name_cn || '',
        bgm_ep_id: null,
        tmdb_season: null,
        tmdb_ep: null,
        tmdb_ep_name: searchEntry.tmdb?.name || '-',
        tvdb_season: null,
        tvdb_ep: null,
        matched,
        media_type: "movie",
      };
    }

    // ── Resolve TVDB ID (specific first, merge all as fallback) ──
    const bgmId = searchEntry?.bangumi?.id;
    const mapEntries: any[] = searchEntry?.map_entries || [];
    const mapEntry = mapEntries.find((me: any) => me.bangumi_id === bgmId);
    const tvdbId: number | undefined = mapEntry?.tvdb_id;

    // Use specific TVDB show if available, otherwise merge all TVDB entries
    let tvdbSeasons: Record<string, any>;
    if (tvdbId != null && tvdbData[String(tvdbId)]) {
      tvdbSeasons = tvdbData[String(tvdbId)].seasons || {};
    } else {
      tvdbSeasons = mergeAllTvdbSeasons(tvdbData);
    }

    // ── Direct TVDB match by season + episode ──
    const seasonData = tvdbSeasons[String(pf.season)];
    const tvdbEp = seasonData?.episodes?.find((e: any) => e.epNum === pf.episode);
    const tvdb_season: number | null = tvdbEp ? pf.season : null;
    const tvdb_ep: number | null = tvdbEp?.epNum ?? null;
    const tvdbEpName: string | null = tvdbEp?.name || null;

    // ── TVDB episode name → fuzzy match TMDB (all merged seasons) ──
    const tmdbMatch = tvdbEpName
      ? fuzzyMatchTmdb(tvdbEpName, "", mergedTmdbSeasons)
      : null;

    const tmdbEpName = tmdbMatch?.name || '';
    const tmdb_season = tmdbMatch?.season ?? null;
    const tmdb_ep = tmdbMatch?.epNum ?? null;

    // ── TMDB episode name → fuzzy match BGM ──
    const bgmMatch = tmdbEpName ? fuzzyMatchBgm(tmdbEpName, allBgmEntries) : null;

    return {
      file_name: pf.file_name,
      torrent_path: pf.torrent_path,
      show_name: pf.show_name,
      src_season: pf.season,
      src_episode: pf.episode,
      bgm_entry: bgmMatch?.bgmEntryName || (searchEntry?.bangumi?.id ? `ID ${searchEntry.bangumi.id}` : '-'),
      bgm_entry_id: bgmMatch?.bgmId ?? searchEntry?.bangumi?.id ?? null,
      bgm_sort: bgmMatch?.bgmEp.sort ?? null,
      bgm_ep_name: bgmMatch?.bgmEp.name || '-',
      bgm_ep_name_cn: bgmMatch?.bgmEp.name_cn || '',
      bgm_ep_id: bgmMatch?.bgmEp.id ?? null,
      tmdb_season,
      tmdb_ep,
      tmdb_ep_name: tmdbEpName || '-',
      tvdb_season,
      tvdb_ep,
      matched: tmdbMatch !== null,
      media_type: "tv",
    };
  });
}

/** Dispatch entry point: selects matching strategy based on data.index. */
export function computeMatches(data: any): MatchRow[] {
  if (data.index === "tmdb") return computeMatchesTmdb(data);
  if (data.index === "tvdb") return computeMatchesTvdb(data);
  // Legacy: no index field — use original Bangumi-first logic
  return computeMatchesLegacy(data);
}

/** Original Bangumi-first matching (legacy, used when data.index is absent). */
export function computeMatchesLegacy(data: any): MatchRow[] {
  // Reuse the existing implementation above by aliasing it
  return _computeMatchesLegacy(data);
}

// Rename the original function for internal use
function _computeMatchesLegacy(data: any): MatchRow[] {
  const parsedFiles: ParsedFile[] = data.parsed_files || [];
  const searchResults: Record<string, SearchEntry> = data.search_results || {};
  const episodeData = data.episode_data || { tmdb: {}, bangumi: {} };

  return parsedFiles.map((pf) => {
    const searchEntry = searchResults[pf.show_name];

    const tmdbId = searchEntry?.tmdb?.id;
    const autoSeasons: Record<string, TmdbSeason> =
      (tmdbId && episodeData.tmdb?.[String(tmdbId)]) || {};
    const tmdbSeasons: Record<string, TmdbSeason> =
      Object.keys(autoSeasons).length > 0
        ? autoSeasons
        : mergeAllTmdbSeasons(episodeData);

    const allBgmEntries = Object.entries(episodeData.bangumi || {}) as [string, BgmEntry][];
    let bgmEp: BgmEpisode | null = null;
    let matchedBgmName = "";
    let matchedBgmId: number | null = null;
    let tmdbMatch: { season: number; epNum: number; name: string } | null = null;

    if (searchEntry?.media_type === "movie") {
      const matched = !!(searchEntry.tmdb && searchEntry.bangumi);
      return {
        file_name: pf.file_name, torrent_path: pf.torrent_path, show_name: pf.show_name,
        src_season: pf.season, src_episode: pf.episode,
        bgm_entry: searchEntry.bangumi?.name || (searchEntry.bangumi?.id ? `ID ${searchEntry.bangumi.id}` : '-'),
        bgm_entry_id: searchEntry.bangumi?.id ?? null,
        bgm_sort: null, bgm_ep_name: searchEntry.bangumi?.name || '-',
        bgm_ep_name_cn: searchEntry.bangumi?.name_cn || '', bgm_ep_id: null,
        tmdb_season: null, tmdb_ep: null, tmdb_ep_name: searchEntry.tmdb?.name || '-',
        tvdb_season: null, tvdb_ep: null,
        matched, media_type: "movie",
      };
    }

    if (pf.season === 0) {
      const tmdbS0 = tmdbSeasons["0"];
      if (tmdbS0) {
        const tmdbEp = tmdbS0.episodes.find((ep) => ep.epNum === pf.episode);
        if (tmdbEp) {
          tmdbMatch = { season: 0, epNum: tmdbEp.epNum, name: tmdbEp.name };
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
      const preferredBgmId = searchEntry?.bangumi?.id;
      const preferredEntry: BgmEntry | undefined =
        (preferredBgmId != null && episodeData.bangumi?.[String(preferredBgmId)]) || undefined;
      if (preferredEntry) {
        const found = (preferredEntry.episodes || []).find((ep) => ep.sort === pf.episode) ?? null;
        if (found) { bgmEp = found; matchedBgmName = preferredEntry.name; matchedBgmId = preferredBgmId!; }
      }
      if (!bgmEp) {
        for (const [bidStr, entry] of allBgmEntries) {
          const found = (entry.episodes || []).find((ep) => ep.sort === pf.episode) ?? null;
          if (found) { bgmEp = found; matchedBgmName = entry.name; matchedBgmId = Number(bidStr); break; }
        }
      }
      if (!bgmEp) {
        const primaryEntry: BgmEntry | undefined =
          (preferredBgmId && episodeData.bangumi?.[String(preferredBgmId)]) || undefined;
        const primaryEps = primaryEntry?.episodes || [];
        if (pf.episode > 0 && pf.episode <= primaryEps.length) {
          bgmEp = primaryEps[pf.episode - 1]; matchedBgmName = primaryEntry?.name || ""; matchedBgmId = preferredBgmId ?? null;
        }
      }
    }

    if (!tmdbMatch) {
      tmdbMatch = bgmEp?.name ? fuzzyMatchTmdb(bgmEp.name, bgmEp.name_cn || "", tmdbSeasons) : null;
    }

    let tvdb_season: number | null = null;
    let tvdb_ep: number | null = null;
    if (bgmEp && matchedBgmId != null) {
      const mapEntries: any[] = searchEntry?.map_entries || [];
      const mapEntry = mapEntries.find((me: any) => me.bangumi_id === matchedBgmId);
      const tvdbId: number | undefined = mapEntry?.tvdb_id;
      if (tvdbId != null) {
        const tvdbSeries = episodeData?.tvdb?.[String(tvdbId)];
        const seasons: Record<string, any> = tvdbSeries?.seasons || {};
        for (const [skey, sdata] of Object.entries(seasons)) {
          const found = (sdata?.episodes || []).find((e: any) => e.absoluteNumber === bgmEp.sort);
          if (found) { tvdb_season = found.seasonNumber ?? Number(skey); tvdb_ep = found.epNum; break; }
        }
        if (tvdb_ep == null && mapEntry?.tvdb_season != null) {
          const targetSeason = seasons[String(mapEntry.tvdb_season)];
          const found = (targetSeason?.episodes || []).find((e: any) => e.epNum === bgmEp.sort);
          if (found) { tvdb_season = mapEntry.tvdb_season; tvdb_ep = found.epNum; }
        }
      }
    }

    return {
      file_name: pf.file_name, torrent_path: pf.torrent_path, show_name: pf.show_name,
      src_season: pf.season, src_episode: pf.episode,
      bgm_entry: matchedBgmName || (searchEntry?.bangumi?.id ? `ID ${searchEntry.bangumi.id}` : '-'),
      bgm_entry_id: matchedBgmId ?? searchEntry?.bangumi?.id ?? null,
      bgm_sort: bgmEp?.sort ?? null, bgm_ep_name: bgmEp?.name || '-',
      bgm_ep_name_cn: bgmEp?.name_cn || '', bgm_ep_id: bgmEp?.id ?? null,
      tmdb_season: tmdbMatch?.season ?? null, tmdb_ep: tmdbMatch?.epNum ?? null,
      tmdb_ep_name: tmdbMatch?.name || '-', tvdb_season, tvdb_ep,
      matched: tmdbMatch !== null, media_type: "tv",
    };
  });
}
