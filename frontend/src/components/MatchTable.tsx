/** Matching logic: parsed_files → search_results → episode_data → table.

   1. parsed_file.show_name → search_results[key]
   2. bangumi.id → episode_data.bangumi[id].episodes (sorted by sort)
   3. parsed_file.episode → positional index → bangumi episode
   4. bangumi ep .name → fuzzy match TMDB episodes across all seasons
   5. Return TMDB season + episode

   BGM Entry / BGM Name columns have dropdowns populated from
   search_results + episode_data so the user can override the
   auto-matched entry and episode.
*/

import { useState, useMemo, useCallback, useRef } from 'react';
import MappingCard from '@/components/Cards/MappingCard';
import type { TmdbSeasonOption, TmdbEpOption } from '@/components/Cards/MappingCard';
import { deleteSubtitle, uploadSubtitle } from '@/api/torrentApi';

// Allowed subtitle extensions for batch folder upload
const BATCH_SUB_EXTENSIONS = new Set(['.ass', '.ssa', '.srt', '.sub', '.idx', '.vtt', '.ttml', '.sbv', '.dfxp']);

/** Extract a candidate episode number from a subtitle filename.
 *  Tries several common anime naming patterns; returns the number or null. */
function extractEpisodeNumber(filename: string): number | null {
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

interface ParsedFile {
  file_name: string;
  torrent_path: string;
  show_name: string;
  season: number;
  episode: number;
}

interface SearchEntry {
  tmdb: { id: number; name: string; original_title?: string; original_name?: string } | null;
  bangumi: { id: number; name: string; name_cn?: string } | null;
  media_type?: "tv" | "movie";
}

interface TmdbEpisode {
  epNum: number;
  tmdbId: number;
  name: string;
  name_cn?: string;
}

interface TmdbSeason {
  name: string;
  episodes: TmdbEpisode[];
}

export interface BgmEpisode {
  sort: number;
  id: number;
  name: string;
  name_cn?: string;
}

interface BgmEntry {
  name: string;
  episodes: BgmEpisode[];
}

export interface MatchRow {
  file_name: string;
  show_name: string;
  src_season: number;
  src_episode: number;
  bgm_entry: string;
  bgm_entry_id: number | null;   // for dropdown default value
  bgm_sort: number | null;
  bgm_ep_name: string;
  bgm_ep_name_cn: string;
  bgm_ep_id: number | null;      // for dropdown default value
  tmdb_season: number | null;
  tmdb_ep: number | null;
  tmdb_ep_name: string;
  matched: boolean;
  media_type?: "tv" | "movie";
}

/**
 * Normalise a string for fuzzy comparison: full-width → half-width
 * for ASCII-range characters (e.g. "！" → "!", "＂" → "\""),
 * then trim and lowercase.
 */
function normalise(s: string): string {
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
function charSimilarity(a: string, b: string): number {
  const sa = new Set(a);
  const sb = new Set(b);
  if (sa.size === 0 && sb.size === 0) return 1;
  let overlap = 0;
  for (const ch of sa) {
    if (sb.has(ch)) overlap++;
  }
  return (2 * overlap) / (sa.size + sb.size);
}

function fuzzyMatchTmdb(
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

export function computeMatches(data: any): MatchRow[] {
  const parsedFiles: ParsedFile[] = data.parsed_files || [];
  const searchResults: Record<string, SearchEntry> = data.search_results || {};
  const episodeData = data.episode_data || { tmdb: {}, bangumi: {} };

  return parsedFiles.map((pf) => {
    const searchEntry = searchResults[pf.show_name];

    const tmdbId = searchEntry?.tmdb?.id;
    const tmdbSeasons: Record<string, TmdbSeason> =
      (tmdbId && episodeData.tmdb?.[String(tmdbId)]) || {};

    // Collect all BGM episodes across ALL bangumi entries (primary + sequels)
    const allBgmEntries = Object.entries(episodeData.bangumi || {}) as [string, BgmEntry][];
    let bgmEp: BgmEpisode | null = null;
    let matchedBgmName = "";
    let matchedBgmId: number | null = null;
    let tmdbMatch: { season: number; epNum: number; name: string } | null = null;

    // ── Movie: backend already matched via TMDB original_title → Bangumi ──
    // No frontend matching needed — just display the results.
    if (searchEntry?.media_type === "movie") {
      const matched = !!(searchEntry.tmdb && searchEntry.bangumi);

      return {
        file_name: pf.file_name,
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
        matched,
        media_type: "movie",
      };
    }

    // ── Season 0 (specials): match TMDB S0 first, then use TMDB ──
    // original name to find the corresponding Bangumi SP episode.
    // This works better than sort-based matching because specials
    // often have non-sequential sort numbers in Bangumi.
    if (pf.season === 0) {
      const tmdbS0 = tmdbSeasons["0"];
      if (tmdbS0) {
        const tmdbEp = tmdbS0.episodes.find((ep) => ep.epNum === pf.episode);
        if (tmdbEp) {
          tmdbMatch = { season: 0, epNum: tmdbEp.epNum, name: tmdbEp.name };
          // Use TMDB original name to find matching BGM episode
          const tmdbNorm = normalise(tmdbEp.name);
          for (const [bidStr, entry] of allBgmEntries) {
            const eps = entry.episodes || [];
            const found = eps.find(
              (ep) => normalise(ep.name) === tmdbNorm,
            );
            if (found) {
              bgmEp = found;
              matchedBgmName = entry.name;
              matchedBgmId = Number(bidStr);
              break;
            }
          }
        }
      }
    }

    // ── Regular season matching (skip if S0 already found BGM) ──
    if (!bgmEp) {
      // First, try the Bangumi entry that was specifically matched to
      // this show name by the backend (respects per-show search results).
      // This prevents multi-season torrents where all files share the
      // same sort numbers from all matching the first Bangumi entry.
      const preferredBgmId = searchEntry?.bangumi?.id;
      const preferredEntry: BgmEntry | undefined =
        (preferredBgmId != null && episodeData.bangumi?.[String(preferredBgmId)]) || undefined;
      if (preferredEntry) {
        const eps = preferredEntry.episodes || [];
        const found = eps.find((ep) => ep.sort === pf.episode) ?? null;
        if (found) {
          bgmEp = found;
          matchedBgmName = preferredEntry.name;
          matchedBgmId = preferredBgmId!;
        }
      }

      // Fallback: search across ALL Bangumi entries (sequels, 番外篇, etc.)
      if (!bgmEp) {
        for (const [bidStr, entry] of allBgmEntries) {
          const eps = entry.episodes || [];
          const found = eps.find((ep) => ep.sort === pf.episode) ?? null;
          if (found) {
            bgmEp = found;
            matchedBgmName = entry.name;
            matchedBgmId = Number(bidStr);
            break;
          }
        }
      }

      // Last-resort fallback: positional index in the primary entry only
      if (!bgmEp) {
        const primaryBgmId = searchEntry?.bangumi?.id;
        const primaryEntry: BgmEntry | undefined =
          (primaryBgmId && episodeData.bangumi?.[String(primaryBgmId)]) || undefined;
        const primaryEps = primaryEntry?.episodes || [];
        if (pf.episode > 0 && pf.episode <= primaryEps.length) {
          bgmEp = primaryEps[pf.episode - 1];
          matchedBgmName = primaryEntry?.name || "";
          matchedBgmId = primaryBgmId ?? null;
        }
      }
    }

    // Fuzzy match BGM name → TMDB (skip if S0 already matched directly)
    if (!tmdbMatch) {
      tmdbMatch = bgmEp?.name
        ? fuzzyMatchTmdb(bgmEp.name, bgmEp.name_cn || "", tmdbSeasons)
        : null;
    }

    return {
      file_name: pf.file_name,
      show_name: pf.show_name,
      src_season: pf.season,
      src_episode: pf.episode,
      bgm_entry: matchedBgmName || (searchEntry?.bangumi?.id ? `ID ${searchEntry.bangumi.id}` : '-'),
      bgm_entry_id: matchedBgmId,
      bgm_sort: bgmEp?.sort ?? null,
      bgm_ep_name: bgmEp?.name || '-',
      bgm_ep_name_cn: bgmEp?.name_cn || '',
      bgm_ep_id: bgmEp?.id ?? null,
      tmdb_season: tmdbMatch?.season ?? null,
      tmdb_ep: tmdbMatch?.epNum ?? null,
      tmdb_ep_name: tmdbMatch?.name || '-',
      matched: tmdbMatch !== null,
      media_type: "tv",
    };
  });
}

export default function MatchTable({ data }: { data: any }) {
  const searchResults: Record<string, SearchEntry> = data.search_results || {};
  const episodeData = data.episode_data || { tmdb: {}, bangumi: {} };
  const subtitles: string[] = data.subtitles || [];
  const torrentName: string = data.torrent_name || '';

  // User-uploaded subtitles: {originalFilename, storedFilename}[]
  const [uploadedSubtitles, setUploadedSubtitles] = useState<
    { originalFilename: string; storedFilename: string }[]
  >([]);
  // Combine torrent subtitles + uploaded stored filenames for matching
  const combinedSubtitles = useMemo(
    () => [...subtitles, ...uploadedSubtitles.map((u) => u.storedFilename)],
    [subtitles, uploadedSubtitles],
  );

  const handleSubtitleUploaded = useCallback(
    (originalFilename: string, storedFilename: string) => {
      setUploadedSubtitles((prev) => [...prev, { originalFilename, storedFilename }]);
    },
    [],
  );

  // Delete callback: takes a stored filename, deletes from server + state
  const makeHandleSubtitleDeleted = useCallback(
    (storedFilename: string) => async () => {
      await deleteSubtitle(torrentName, storedFilename);
      setUploadedSubtitles((prev) => prev.filter((u) => u.storedFilename !== storedFilename));
    },
    [torrentName],
  );

  // ── Check whether a video file has a matching subtitle file ──
  // Match by filename stem (name without extension).
  const hasMatchingSubtitle = (videoFileName: string): boolean => {
    const videoStem = videoFileName.replace(/\.[^.]+$/, '').toLowerCase();
    return combinedSubtitles.some(
      (sub) => sub.replace(/\.[^.]+$/, '').toLowerCase() === videoStem,
    );
  };

  // Check whether the matching subtitle is user-uploaded (deletable)
  const isUploadedMatch = (videoFileName: string): boolean => {
    const videoStem = videoFileName.replace(/\.[^.]+$/, '').toLowerCase();
    return uploadedSubtitles.some(
      (u) => u.storedFilename.replace(/\.[^.]+$/, '').toLowerCase() === videoStem,
    );
  };

  // Get the stored filename of the upload that matches this video
  const getUploadedStoredFilename = (videoFileName: string): string | null => {
    const videoStem = videoFileName.replace(/\.[^.]+$/, '').toLowerCase();
    const match = uploadedSubtitles.find(
      (u) => u.storedFilename.replace(/\.[^.]+$/, '').toLowerCase() === videoStem,
    );
    return match?.storedFilename ?? null;
  };

  // ── Build BGM entry dropdown options ──
  // Combine entries from search_results (primary matches) and
  // episode_data.bangumi (auto-fetched sequels / 番外篇), deduped by ID.
  const bgmEntryOptions = useMemo(() => {
    const options: { id: number; name: string }[] = [];
    const seen = new Set<number>();

    // From search_results (primary per-show-name matches)
    for (const entry of Object.values(searchResults)) {
      if (entry.bangumi?.id && !seen.has(entry.bangumi.id)) {
        seen.add(entry.bangumi.id);
        options.push({
          id: entry.bangumi.id,
          name: entry.bangumi.name_cn || entry.bangumi.name || `ID ${entry.bangumi.id}`,
        });
      }
    }

    // From episode_data.bangumi (includes sequels, 番外篇, etc.)
    const bgmData: Record<string, BgmEntry> = episodeData.bangumi || {};
    for (const [idStr, entry] of Object.entries(bgmData)) {
      const id = Number(idStr);
      if (!seen.has(id)) {
        seen.add(id);
        options.push({ id, name: entry.name || `ID ${id}` });
      }
    }

    return options.sort((a, b) => a.name.localeCompare(b.name));
  }, [searchResults, episodeData]);

  // ── Initial auto-computed rows (regular files only) ──
  const initialRows = useMemo(() => {
    const regularRows = computeMatches(data);
    const specials: any[] = data.specials || [];
    // SP/Extra files are returned as-is without anitopy re-parsing.
    // All dropdowns start empty — the user manually selects everything.
    const spRows: MatchRow[] = specials.map((s: any) => ({
      file_name: s.file_name,
      show_name: s.show_name || '-',
      src_season: s.season ?? 0,
      src_episode: s.episode ?? 0,
      bgm_entry: '-',
      bgm_entry_id: null,
      bgm_sort: null,
      bgm_ep_name: '-',
      bgm_ep_name_cn: '',
      bgm_ep_id: null,
      tmdb_season: null,
      tmdb_ep: null,
      tmdb_ep_name: '-',
      matched: false,
      media_type: "special" as any,
    }));
    return [...regularRows, ...spRows];
  }, [data]);

  // ── Per-row overrides: rowIndex → { bgmEntryId, bgmEpSort, bgmEpId?, ... } ──
  // TMDB fields allow direct season/episode override independent of BGM matching.
  // bgmEpId stores the exact episode ID for disambiguation when episodes share
  // the same sort number (e.g. two specials both with sort=1).
  const [overrides, setOverrides] = useState<
    Record<number, { bgmEntryId: number; bgmEpSort: number; bgmEpId?: number; tmdbSeason?: number; tmdbEp?: number; tmdbShowId?: number; manualMatched?: boolean }>
  >({});

  // ── Get episodes for a specific BGM entry ──
  const getBgmEpisodes = (entryId: number): BgmEpisode[] => {
    const bgmData: Record<string, BgmEntry> = episodeData.bangumi || {};
    return bgmData[String(entryId)]?.episodes || [];
  };

  // ── Handle BGM Entry dropdown change → reset episode to first ──
  const handleBgmEntryChange = (rowIndex: number, entryIdStr: string) => {
    const entryId = Number(entryIdStr);
    const eps = getBgmEpisodes(entryId);
    const firstEp = eps[0];
    setOverrides((prev) => {
      const existing = prev[rowIndex] || {};
      return {
        ...prev,
        [rowIndex]: {
          ...existing,
          bgmEntryId: entryId,
          bgmEpSort: firstEp?.sort ?? 0,
          bgmEpId: firstEp?.id,  // store exact episode ID for disambiguation
        },
      };
    });
  };

  // ── Handle BGM Name (episode) dropdown change ──
  // Uses episode ID as value (not sort) because sort numbers can be duplicated
  // within the same BGM entry (e.g. two specials both with sort=1).
  const handleBgmEpChange = (rowIndex: number, entryId: number, epIdStr: string) => {
    const epId = Number(epIdStr);
    const eps = getBgmEpisodes(entryId);
    const ep = eps.find(e => e.id === epId);
    setOverrides((prev) => {
      const existing = prev[rowIndex] || {};
      return {
        ...prev,
        [rowIndex]: {
          ...existing,
          bgmEntryId: entryId,
          bgmEpSort: ep?.sort ?? 0,
          bgmEpId: epId,  // store exact episode ID for disambiguation
        },
      };
    });
  };

  // ── Handle TMDB Season dropdown change → auto-select first episode ──
  // SP cards pass a composite "tmdbId:season" value to allow selecting
  // seasons from ANY show, not just the one matched to the SP file's show_name.
  const handleTmdbSeasonChange = (rowIndex: number, showName: string, seasonStr: string) => {
    // SP composite value: "tmdbId:season"
    if (seasonStr.includes(":")) {
      const [tmdbIdStr, seasonStr2] = seasonStr.split(":");
      const tmdbShowId = Number(tmdbIdStr);
      const season = Number(seasonStr2);
      const tmdbSeasons: Record<string, TmdbSeason> =
        episodeData.tmdb?.[String(tmdbShowId)] || {};
      const seasonData = tmdbSeasons[String(season)];
      const sortedEps = [...(seasonData?.episodes || [])].sort((a, b) => a.epNum - b.epNum);
      const firstEp = sortedEps[0]?.epNum;
      setOverrides((prev) => {
        const existing = prev[rowIndex];
        return {
          ...prev,
          [rowIndex]: {
            bgmEntryId: existing?.bgmEntryId ?? 0,
            bgmEpSort: existing?.bgmEpSort ?? 0,
            tmdbSeason: season,
            tmdbEp: firstEp,
            tmdbShowId: tmdbShowId,
          },
        };
      });
      return;
    }

    // Regular card: use the show's own TMDB match
    const season = Number(seasonStr);
    const tmdbId = searchResults[showName]?.tmdb?.id;
    const tmdbSeasons: Record<string, TmdbSeason> =
      (tmdbId && episodeData.tmdb?.[String(tmdbId)]) || {};
    const seasonData = tmdbSeasons[String(season)];
    const sortedEps = [...(seasonData?.episodes || [])].sort((a, b) => a.epNum - b.epNum);
    const firstEp = sortedEps[0]?.epNum;
    setOverrides((prev) => {
      const existing = prev[rowIndex];
      return {
        ...prev,
        [rowIndex]: {
          bgmEntryId: existing?.bgmEntryId ?? 0,
          bgmEpSort: existing?.bgmEpSort ?? 0,
          tmdbSeason: season,
          tmdbEp: firstEp,
        },
      };
    });
  };

  // ── Handle badge click: toggle matched ↔ pending ──
  const handleToggleMatched = (rowIndex: number, currentMatched: boolean) => {
    setOverrides((prev) => {
      const existing = prev[rowIndex] || { bgmEntryId: 0, bgmEpSort: 0 };
      return {
        ...prev,
        [rowIndex]: { ...existing, manualMatched: !currentMatched },
      };
    });
  };

  // ── Handle TMDB Episode dropdown change ──
  const handleTmdbEpChange = (rowIndex: number, epStr: string) => {
    const ep = Number(epStr);
    setOverrides((prev) => {
      const existing = prev[rowIndex];
      return {
        ...prev,
        [rowIndex]: {
          bgmEntryId: existing?.bgmEntryId ?? 0,
          bgmEpSort: existing?.bgmEpSort ?? 0,
          tmdbSeason: existing?.tmdbSeason ?? 0,
          tmdbEp: ep,
          tmdbShowId: existing?.tmdbShowId,  // preserve SP cross-show binding
        },
      };
    });
  };

  // ── Effective rows (apply overrides + re-compute TMDB match) ──
  const rows = useMemo(() => {
    return initialRows.map((r, i) => {
      const ov = overrides[i];
      if (!ov) return r;

      // Helper: apply manualMatched toggle if set, otherwise keep computed value
      const applyManualMatched = (row: MatchRow, computedMatched: boolean): MatchRow => {
        if (ov.manualMatched !== undefined) {
          return { ...row, matched: ov.manualMatched };
        }
        return row;
      };

      // Look up the overridden BGM entry
      const ovEntry = bgmEntryOptions.find((e) => e.id === ov.bgmEntryId);
      const eps = getBgmEpisodes(ov.bgmEntryId);
      // Prefer exact episode ID lookup to disambiguate when multiple
      // episodes share the same sort number (e.g. two specials both sort=1).
      const ovEp = ov.bgmEpId != null
        ? eps.find((e) => e.id === ov.bgmEpId)
        : eps.find((e) => e.sort === ov.bgmEpSort);

      // Movies: override only updates BGM entry — no episodes to match against
      if (r.media_type === "movie") {
        return applyManualMatched({
          ...r,
          bgm_entry: ovEntry?.name || `ID ${ov.bgmEntryId}`,
          bgm_entry_id: ov.bgmEntryId,
          bgm_ep_name: ovEntry?.name || r.bgm_ep_name,
          bgm_ep_name_cn: '',
          bgm_ep_id: null,
          bgm_sort: null,
        }, r.matched);
      }

      // ── Resolve TMDB lookup data shared by BGM→TMDB and direct overrides ──
      // SP rows may have a cross-show tmdbShowId override for manual mapping.
      const tmdbId = ov.tmdbShowId ?? searchResults[r.show_name]?.tmdb?.id;
      const tmdbSeasons: Record<string, TmdbSeason> =
        (tmdbId && episodeData.tmdb?.[String(tmdbId)]) || {};

      if (!ovEp) {
        // No BGM episode matched — TMDB override may still apply independently
        if (ov.tmdbSeason != null && ov.tmdbEp != null) {
          const sData = tmdbSeasons[String(ov.tmdbSeason)];
          const eData = sData?.episodes?.find(e => e.epNum === ov.tmdbEp);
          return applyManualMatched({
            ...r,
            tmdb_season: ov.tmdbSeason,
            tmdb_ep: ov.tmdbEp,
            tmdb_ep_name: eData?.name || '-',
            matched: true,
          }, true);
        }
        return applyManualMatched(r, r.matched);
      }

      // Re-compute TMDB match against the overridden BGM episode name
      const tmdbMatch = fuzzyMatchTmdb(
        ovEp.name,
        ovEp.name_cn || "",
        tmdbSeasons,
      );

      let finalSeason = tmdbMatch?.season ?? null;
      let finalEp = tmdbMatch?.epNum ?? null;
      let finalEpName = tmdbMatch?.name || '-';
      let finalMatched = tmdbMatch !== null;

      // TMDB direct override takes precedence over auto-computed match
      if (ov.tmdbSeason != null && ov.tmdbEp != null) {
        finalSeason = ov.tmdbSeason;
        finalEp = ov.tmdbEp;
        finalMatched = true;
        const sData = tmdbSeasons[String(ov.tmdbSeason)];
        const eData = sData?.episodes?.find(e => e.epNum === ov.tmdbEp);
        finalEpName = eData?.name || '-';
      }

      return applyManualMatched({
        ...r,
        bgm_entry: ovEntry?.name || `ID ${ov.bgmEntryId}`,
        bgm_entry_id: ov.bgmEntryId,
        bgm_sort: ovEp.sort,
        bgm_ep_name: ovEp.name,
        bgm_ep_name_cn: ovEp.name_cn || '',
        bgm_ep_id: ovEp.id,
        tmdb_season: finalSeason,
        tmdb_ep: finalEp,
        tmdb_ep_name: finalEpName,
        matched: finalMatched,
      }, finalMatched);
    });
  }, [initialRows, overrides, bgmEntryOptions, searchResults, episodeData]);

  // ── Split rows into movie / TV tables (preserve original indices for overrides) ──
  const movieRows = useMemo(
    () => rows.map((r, i) => ({ ...r, _idx: i })).filter((r) => r.media_type === "movie"),
    [rows],
  );
  const tvRows = useMemo(
    () => rows.map((r, i) => ({ ...r, _idx: i })).filter((r) => r.media_type === "tv"),
    [rows],
  );
  const spRows = useMemo(
    () => rows.map((r, i) => ({ ...r, _idx: i })).filter((r) => r.media_type === "special"),
    [rows],
  );

  // ── Batch folder subtitle upload ──
  const batchFolderRef = useRef<HTMLInputElement>(null);
  const [batchProcessing, setBatchProcessing] = useState(false);
  const [batchProgress, setBatchProgress] = useState('');

  const handleBatchFolderUpload = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;

    setBatchProcessing(true);
    setBatchProgress('');

    // Filter to subtitle files only, preserving relative paths
    const subFiles: { file: File; relativePath: string }[] = [];
    for (let i = 0; i < files.length; i++) {
      const f = files[i];
      const ext = '.' + f.name.split('.').pop()?.toLowerCase();
      if (BATCH_SUB_EXTENSIONS.has(ext)) {
        // webkitRelativePath contains the folder-relative path; fall back to name
        const relPath = (f as any).webkitRelativePath || f.name;
        subFiles.push({ file: f, relativePath: relPath });
      }
    }

    if (subFiles.length === 0) {
      setBatchProgress('文件夹中未找到字幕文件');
      setBatchProcessing(false);
      if (batchFolderRef.current) batchFolderRef.current.value = '';
      return;
    }

    // Build a map: episode number → TV row (use src_episode for matching)
    const epToRow = new Map<number, typeof tvRows[0]>();
    for (const row of tvRows) {
      const ep = row.src_episode;
      if (ep != null && ep > 0 && !epToRow.has(ep)) {
        epToRow.set(ep, row);
      }
    }

    let matched = 0;
    let skipped = 0;
    const errors: string[] = [];

    for (const { file, relativePath } of subFiles) {
      const epNum = extractEpisodeNumber(relativePath);
      if (epNum === null) {
        skipped++;
        continue;
      }

      const targetRow = epToRow.get(epNum);
      if (!targetRow) {
        skipped++;
        continue;
      }

      // Compute the video stem so the stored subtitle matches the badge logic
      const videoStem = targetRow.file_name.replace(/\.[^.]+$/, '');

      try {
        const result = await uploadSubtitle(file, torrentName, videoStem);
        // Add to state — the stored filename stem matches the video stem,
        // so hasMatchingSubtitle will pick it up automatically
        setUploadedSubtitles((prev) => [...prev, {
          originalFilename: file.name,
          storedFilename: result.filename,
        }]);
        matched++;
      } catch (err: any) {
        errors.push(`${file.name}: ${err.message}`);
      }
    }

    let msg = `匹配 ${matched} 个字幕`;
    if (skipped > 0) msg += `，跳过 ${skipped} 个`;
    if (errors.length > 0) msg += `，${errors.length} 个失败`;
    setBatchProgress(msg);

    setBatchProcessing(false);
    if (batchFolderRef.current) batchFolderRef.current.value = '';
  }, [tvRows, torrentName]);

  return (
    <div className="space-y-10">
      {/* ── Movie Table ── */}
      {movieRows.length > 0 && (
        <div className="mb-10">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-primary">
                <rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18" />
                <line x1="7" y1="2" x2="7" y2="22" />
                <line x1="17" y1="2" x2="17" y2="22" />
                <line x1="2" y1="12" x2="22" y2="12" />
                <line x1="2" y1="7" x2="7" y2="7" />
                <line x1="2" y1="17" x2="7" y2="17" />
                <line x1="17" y1="7" x2="22" y2="7" />
                <line x1="17" y1="17" x2="22" y2="17" />
              </svg>
              <h3 className="font-bold text-lg">Movies</h3>
              <span className="text-xs text-slate-400 ml-2">({movieRows.length} files)</span>
            </div>
          </div>
          <div className="space-y-3">
            {movieRows.map((r) => {
              const i = (r as any)._idx as number;
              const currentEntryId = r.bgm_entry_id ?? 0;
              const currentEps = r.bgm_entry_id ? getBgmEpisodes(r.bgm_entry_id) : [];

              return (
                <MappingCard
                  key={i}
                  row={r}
                  rowIndex={i}
                  variant="movie"
                  hasSubtitle={hasMatchingSubtitle(r.file_name)}
                  isUploadedSubtitle={isUploadedMatch(r.file_name)}
                  torrentName={torrentName}
                  onSubtitleUploaded={handleSubtitleUploaded}
                  onSubtitleDeleted={
                    (() => {
                      const sf = getUploadedStoredFilename(r.file_name);
                      return sf ? makeHandleSubtitleDeleted(sf) : undefined;
                    })()
                  }
                  bgmEntryOptions={bgmEntryOptions}
                  currentEps={currentEps}
                  currentEntryId={currentEntryId}
                  onBgmEntryChange={(v) => handleBgmEntryChange(i, v)}
                  onToggleMatched={() => handleToggleMatched(i, r.matched)}
                />
              );
            })}
          </div>
        </div>
      )}

      {/* ── TV Cards ── */}
      {tvRows.length > 0 && (
        <div className="mb-10">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-primary">
                <rect x="2" y="7" width="20" height="15" rx="2" ry="2" />
                <polyline points="17 2 12 7 7 2" />
              </svg>
              <h3 className="font-bold text-lg">TV Series</h3>
              <span className="text-xs text-slate-400 ml-2">({tvRows.length} files)</span>
            </div>
            <div className="flex items-center gap-3">
              {/* Batch folder upload */}
              <input
                ref={batchFolderRef}
                type="file"
                // @ts-ignore — webkitdirectory is widely supported but not in TS types
                webkitdirectory=""
                directory=""
                accept=".ass,.ssa,.srt,.sub,.idx,.vtt,.ttml,.sbv,.dfxp"
                className="hidden"
                onChange={handleBatchFolderUpload}
              />
              <button
                className="inline-flex items-center gap-1.5 bg-[#f09199]/10 text-[#f09199] text-[10px] px-3 py-1 rounded-full font-bold uppercase tracking-wider hover:bg-[#f09199]/25 transition-colors cursor-pointer disabled:opacity-50"
                title="批量上传字幕文件夹 — 自动按集数匹配"
                onClick={() => batchFolderRef.current?.click()}
                disabled={batchProcessing}
              >
                {batchProcessing ? (
                  <>
                    <div className="w-3 h-3 border-2 border-[#f09199]/30 border-t-[#f09199] rounded-full animate-spin" />
                    匹配中...
                  </>
                ) : (
                  '+SUB'
                )}
              </button>
            </div>
          </div>
          {/* Batch progress message */}
          {batchProgress && (
            <p className="text-xs text-slate-500 mb-3 -mt-1">{batchProgress}</p>
          )}
          <div className="space-y-3">
            {tvRows.map((r) => {
              const i = (r as any)._idx as number;
              const currentEps = r.bgm_entry_id ? getBgmEpisodes(r.bgm_entry_id) : [];
              const currentEntryId = r.bgm_entry_id ?? 0;

              // Pre-compute TMDB season options for this TV card
              const tmdbId = searchResults[r.show_name]?.tmdb?.id;
              const rowTmdbSeasons: Record<string, TmdbSeason> =
                (tmdbId && episodeData.tmdb?.[String(tmdbId)]) || {};
              const tmdbSeasonOpts: TmdbSeasonOption[] = Object.entries(rowTmdbSeasons).map(([skey, sdata]) => ({
                value: String(Number(skey)),
                label: sdata.name || `Season ${skey}`,
              }));

              // Pre-compute TMDB episode options
              const selSeasonKey = r.tmdb_season != null ? String(r.tmdb_season) : '';
              const selSeasonData = selSeasonKey ? rowTmdbSeasons[selSeasonKey] : null;
              const tmdbEpOpts: TmdbEpOption[] = (selSeasonData?.episodes || [])
                .sort((a, b) => a.epNum - b.epNum);

              return (
                <MappingCard
                  key={i}
                  row={r}
                  rowIndex={i}
                  variant="tv"
                  hasSubtitle={hasMatchingSubtitle(r.file_name)}
                  isUploadedSubtitle={isUploadedMatch(r.file_name)}
                  torrentName={torrentName}
                  onSubtitleUploaded={handleSubtitleUploaded}
                  onSubtitleDeleted={
                    (() => {
                      const sf = getUploadedStoredFilename(r.file_name);
                      return sf ? makeHandleSubtitleDeleted(sf) : undefined;
                    })()
                  }
                  bgmEntryOptions={bgmEntryOptions}
                  currentEps={currentEps}
                  currentEntryId={currentEntryId}
                  tmdbSeasonOptions={tmdbSeasonOpts}
                  tmdbSeasonValue={r.tmdb_season ?? ''}
                  tmdbEpOptions={tmdbEpOpts}
                  tmdbEpValue={r.tmdb_ep ?? ''}
                  tmdbEpTitle={r.tmdb_ep_name}
                  onBgmEntryChange={(v) => handleBgmEntryChange(i, v)}
                  onBgmEpChange={(v) => handleBgmEpChange(i, currentEntryId, v)}
                  onTmdbSeasonChange={(v) => handleTmdbSeasonChange(i, r.show_name, v)}
                  onTmdbEpChange={(v) => handleTmdbEpChange(i, v)}
                  onToggleMatched={() => handleToggleMatched(i, r.matched)}
                />
              );
            })}
          </div>
        </div>
      )}

      {/* ── SP / Extras Cards ── */}
      {spRows.length > 0 && (
        <div className="mb-10">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-amber-500">
                <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
              </svg>
              <h3 className="font-bold text-lg">SP / Extras</h3>
              <span className="text-xs text-slate-400 ml-2">({spRows.length} files)</span>
            </div>
          </div>
          <div className="space-y-3">
            {spRows.map((r) => {
              const i = (r as any)._idx as number;
              const currentEps = r.bgm_entry_id ? getBgmEpisodes(r.bgm_entry_id) : [];
              const currentEntryId = r.bgm_entry_id ?? 0;

              // Pre-compute TMDB season options: aggregate ALL seasons from all shows
              const tmdbSeasonOpts: TmdbSeasonOption[] = [];
              for (const [tmdbIdStr, seasons] of Object.entries(episodeData.tmdb || {})) {
                const showTmdbId = Number(tmdbIdStr);
                let showLabel = '';
                for (const [, entry] of Object.entries(searchResults)) {
                  if (entry.tmdb?.id === showTmdbId) {
                    showLabel = entry.tmdb.name;
                    break;
                  }
                }
                if (!showLabel) showLabel = `TMDB ${showTmdbId}`;
                for (const [skey, sdata] of Object.entries(seasons)) {
                  tmdbSeasonOpts.push({
                    value: `${showTmdbId}:${skey}`,
                    label: `${showLabel}  ${sdata.name || `Season ${skey}`}`,
                  });
                }
              }

              // SP TMDB season value may be a composite "tmdbId:season"
              const ov = overrides[i];
              const tmdbSeasonVal = ov?.tmdbShowId && ov.tmdbSeason != null
                ? `${ov.tmdbShowId}:${ov.tmdbSeason}`
                : (r.tmdb_season ?? '');

              // Pre-compute TMDB episode options (SP uses override's tmdbShowId)
              const lookupTmdbId = ov?.tmdbShowId ?? searchResults[r.show_name]?.tmdb?.id;
              const lookupSeasons: Record<string, TmdbSeason> =
                (lookupTmdbId && episodeData.tmdb?.[String(lookupTmdbId)]) || {};
              const selSeasonKey = r.tmdb_season != null ? String(r.tmdb_season) : '';
              const selSeasonData = selSeasonKey ? lookupSeasons[selSeasonKey] : null;
              const tmdbEpOpts: TmdbEpOption[] = (selSeasonData?.episodes || [])
                .sort((a, b) => a.epNum - b.epNum);

              return (
                <MappingCard
                  key={i}
                  row={r}
                  rowIndex={i}
                  variant="sp"
                  hasSubtitle={hasMatchingSubtitle(r.file_name)}
                  isUploadedSubtitle={isUploadedMatch(r.file_name)}
                  torrentName={torrentName}
                  onSubtitleUploaded={handleSubtitleUploaded}
                  onSubtitleDeleted={
                    (() => {
                      const sf = getUploadedStoredFilename(r.file_name);
                      return sf ? makeHandleSubtitleDeleted(sf) : undefined;
                    })()
                  }
                  bgmEntryOptions={bgmEntryOptions}
                  currentEps={currentEps}
                  currentEntryId={currentEntryId}
                  tmdbSeasonOptions={tmdbSeasonOpts}
                  tmdbSeasonValue={tmdbSeasonVal}
                  tmdbEpOptions={tmdbEpOpts}
                  tmdbEpValue={r.tmdb_ep ?? ''}
                  tmdbEpTitle={r.tmdb_ep_name}
                  onBgmEntryChange={(v) => handleBgmEntryChange(i, v)}
                  onBgmEpChange={(v) => handleBgmEpChange(i, currentEntryId, v)}
                  onTmdbSeasonChange={(v) => handleTmdbSeasonChange(i, r.show_name, v)}
                  onTmdbEpChange={(v) => handleTmdbEpChange(i, v)}
                  onToggleMatched={() => handleToggleMatched(i, r.matched)}
                />
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
