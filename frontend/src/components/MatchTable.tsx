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

import { useState, useMemo } from 'react';

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

interface BgmEpisode {
  sort: number;
  id: number;
  name: string;
  name_cn?: string;
}

interface BgmEntry {
  name: string;
  episodes: BgmEpisode[];
}

interface MatchRow {
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
    // SP/Extra directory files have all dropdowns starting empty —
    // the user manually selects BGM entry / episode / TMDB mapping.
    const spRows: MatchRow[] = specials.map((s: any) => ({
      file_name: s.file_name,
      show_name: s.show_name,
      src_season: s.season,
      src_episode: s.episode,
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

  // ── Per-row overrides: rowIndex → { bgmEntryId, bgmEpSort, tmdbSeason?, tmdbEp? } ──
  // TMDB fields allow direct season/episode override independent of BGM matching.
  const [overrides, setOverrides] = useState<
    Record<number, { bgmEntryId: number; bgmEpSort: number; tmdbSeason?: number; tmdbEp?: number; tmdbShowId?: number }>
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
    const firstSort = eps[0]?.sort ?? 0;
    setOverrides((prev) => ({
      ...prev,
      [rowIndex]: { bgmEntryId: entryId, bgmEpSort: firstSort },
    }));
  };

  // ── Handle BGM Name (episode) dropdown change ──
  const handleBgmEpChange = (rowIndex: number, entryId: number, epSortStr: string) => {
    setOverrides((prev) => ({
      ...prev,
      [rowIndex]: { bgmEntryId: entryId, bgmEpSort: Number(epSortStr) },
    }));
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

      // Look up the overridden BGM entry
      const ovEntry = bgmEntryOptions.find((e) => e.id === ov.bgmEntryId);
      const eps = getBgmEpisodes(ov.bgmEntryId);
      const ovEp = eps.find((e) => e.sort === ov.bgmEpSort);

      // Movies: override only updates BGM entry — no episodes to match against
      if (r.media_type === "movie") {
        return {
          ...r,
          bgm_entry: ovEntry?.name || `ID ${ov.bgmEntryId}`,
          bgm_entry_id: ov.bgmEntryId,
          bgm_ep_name: ovEntry?.name || r.bgm_ep_name,
          bgm_ep_name_cn: '',
          bgm_ep_id: null,
          bgm_sort: null,
        };
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
          return {
            ...r,
            tmdb_season: ov.tmdbSeason,
            tmdb_ep: ov.tmdbEp,
            tmdb_ep_name: eData?.name || '-',
            matched: true,
          };
        }
        return r;
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

      return {
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
      };
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
          <div className="bg-surface-light dark:bg-surface-dark border border-border-light dark:border-border-dark rounded-xl overflow-hidden shadow-sm">
            <table className="w-full text-left border-collapse">
              <thead className="bg-slate-50 dark:bg-white/5 text-[11px] font-bold text-slate-400 uppercase tracking-wider">
                <tr>
                  <th className="px-4 py-3 border-b border-border-light dark:border-border-dark">File Name</th>
                  <th className="px-4 py-3 border-b border-border-light dark:border-border-dark">BGM Entry</th>
                  <th className="px-4 py-3 border-b border-border-light dark:border-border-dark">TMDB Name</th>
                  <th className="px-4 py-3 border-b border-border-light dark:border-border-dark text-right">Status</th>
                </tr>
              </thead>
              <tbody className="text-sm font-medium divide-y divide-border-light dark:divide-border-dark">
                {movieRows.map((r) => {
                  const i = (r as any)._idx as number;
                  const currentEntryId = r.bgm_entry_id ?? 0;
                  return (
                    <tr key={i} className="table-row-hover group">
                      <td className="px-4 py-3 font-mono text-[12px] max-w-xs truncate">{r.file_name}</td>
                      <td className="px-4 py-3">
                        <select
                          className="text-xs py-1 bg-transparent border-slate-200 dark:border-white/10 rounded w-full max-w-[100px] truncate"
                          value={currentEntryId || ''}
                          onChange={(e) => {
                            const entryId = Number(e.target.value);
                            setOverrides((prev) => ({
                              ...prev,
                              [i]: { bgmEntryId: entryId, bgmEpSort: 0 },
                            }));
                          }}
                        >
                          {!currentEntryId && <option value="" disabled>-</option>}
                          {bgmEntryOptions.map((opt) => (
                            <option key={opt.id} value={opt.id}>{opt.name}</option>
                          ))}
                        </select>
                      </td>
                      <td className="px-4 py-3 text-slate-500 max-w-[250px] truncate">{r.tmdb_ep_name}</td>
                      <td className="px-4 py-3 text-right">
                        {r.matched ? (
                          <span className="bg-primary/10 text-primary text-[10px] px-2 py-1 rounded font-bold uppercase">Mapped</span>
                        ) : (
                          <span className="bg-amber-500/10 text-amber-500 text-[10px] px-2 py-1 rounded font-bold uppercase">Pending</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
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
          </div>
          <div className="space-y-3">
            {tvRows.map((r) => {
              const i = (r as any)._idx as number;
              const currentEps = r.bgm_entry_id ? getBgmEpisodes(r.bgm_entry_id) : [];
              const currentEntryId = r.bgm_entry_id ?? 0;
              return (
                <div key={i} className="bg-surface-light dark:bg-surface-dark border border-border-light dark:border-border-dark rounded-xl overflow-hidden shadow-sm transition-all hover:shadow-md group">
                  {/* Top row: file name + badges */}
                  <div className="px-4 py-3 border-b border-slate-50 dark:border-white/5">
                    <div className="flex items-center justify-between gap-4">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-3">
                          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-slate-300 shrink-0">
                            <path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z" />
                            <polyline points="14 2 14 8 20 8" />
                          </svg>
                          <h4 className="font-mono text-xs font-bold text-slate-700 dark:text-slate-200 truncate">{r.file_name}</h4>
                        </div>
                      </div>
                      <div className="flex items-center gap-4 shrink-0">
                        <div className="flex items-center gap-1.5 px-2 py-1 bg-slate-50 dark:bg-white/5 rounded-md">
                          <span className="text-[10px] text-slate-400 font-bold uppercase tracking-tighter">Show</span>
                          <span className="text-xs font-semibold text-slate-600 dark:text-slate-300">{r.show_name}</span>
                        </div>
                        <div className="flex items-center gap-1.5 px-2 py-1 bg-slate-50 dark:bg-white/5 rounded-md">
                          <span className="text-[10px] text-slate-400 font-bold uppercase tracking-tighter">S/E</span>
                          <span className="text-xs font-semibold text-slate-600 dark:text-slate-300">{r.src_season} / {r.src_episode}</span>
                        </div>
                      </div>
                    </div>
                  </div>
                  {/* Bottom row: mapping controls */}
                  <div className="px-4 py-2.5 bg-slate-50/30 dark:bg-white/[0.02] flex flex-wrap items-center gap-x-6 gap-y-2">
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] text-slate-400 font-bold uppercase tracking-tighter">BGM Entry</span>
                      <select
                        className="text-[11px] py-0.5 px-1 bg-transparent border-slate-200 dark:border-white/10 rounded font-medium max-w-[100px] truncate focus:ring-1 focus:ring-primary/30 cursor-pointer"
                        value={currentEntryId || ''}
                        onChange={(e) => handleBgmEntryChange(i, e.target.value)}
                      >
                        {!currentEntryId && <option value="" disabled>-</option>}
                        {bgmEntryOptions.map((opt) => (
                          <option key={opt.id} value={opt.id}>{opt.name}</option>
                        ))}
                      </select>
                      <div className="flex items-center gap-1 ml-1">
                        <span className="text-[10px] text-slate-400 font-bold uppercase tracking-tighter">#</span>
                        <span className="text-[11px] font-mono text-slate-500">{r.bgm_sort ?? '-'}</span>
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] text-slate-400 font-bold uppercase tracking-tighter">BGM Name</span>
                      <select
                        className="text-[11px] py-0.5 px-1 bg-transparent border-slate-200 dark:border-white/10 rounded font-medium max-w-[220px] truncate focus:ring-1 focus:ring-primary/30 cursor-pointer"
                        value={r.bgm_sort ?? ''}
                        onChange={(e) => handleBgmEpChange(i, currentEntryId, e.target.value)}
                        title={`${r.bgm_ep_name}${r.bgm_ep_name_cn ? ` / ${r.bgm_ep_name_cn}` : ''}`}
                      >
                        {currentEps.length === 0 && (
                          <option value="" disabled>{r.bgm_ep_name || '-'}</option>
                        )}
                        {currentEps.map((ep) => (
                          <option key={ep.sort} value={ep.sort}>
                            E{ep.sort} {ep.name}{ep.name_cn ? ` / ${ep.name_cn}` : ''}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] text-slate-400 font-bold uppercase tracking-tighter">TMDB S</span>
                      <select
                        className="text-[11px] py-0.5 px-1 bg-transparent border-slate-200 dark:border-white/10 rounded font-medium focus:ring-1 focus:ring-primary/30 cursor-pointer"
                        value={r.tmdb_season ?? ''}
                        onChange={(e) => handleTmdbSeasonChange(i, r.show_name, e.target.value)}
                      >
                        {r.tmdb_season == null && <option value="" disabled>-</option>}
                        {(() => {
                          const tmdbId = searchResults[r.show_name]?.tmdb?.id;
                          const rowTmdbSeasons: Record<string, TmdbSeason> =
                            (tmdbId && episodeData.tmdb?.[String(tmdbId)]) || {};
                          return Object.entries(rowTmdbSeasons).map(([skey, sdata]) => (
                            <option key={skey} value={Number(skey)}>{sdata.name || `Season ${skey}`}</option>
                          ));
                        })()}
                      </select>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] text-slate-400 font-bold uppercase tracking-tighter">TMDB Ep</span>
                      <select
                        className="text-[11px] py-0.5 px-1 bg-transparent border-slate-200 dark:border-white/10 rounded font-medium max-w-[220px] truncate focus:ring-1 focus:ring-primary/30 cursor-pointer"
                        value={r.tmdb_ep ?? ''}
                        onChange={(e) => handleTmdbEpChange(i, e.target.value)}
                        title={r.tmdb_ep_name}
                      >
                        {(() => {
                          const tmdbId = searchResults[r.show_name]?.tmdb?.id;
                          const rowTmdbSeasons: Record<string, TmdbSeason> =
                            (tmdbId && episodeData.tmdb?.[String(tmdbId)]) || {};
                          const selSeasonKey = r.tmdb_season != null ? String(r.tmdb_season) : '';
                          const selSeasonData = selSeasonKey ? rowTmdbSeasons[selSeasonKey] : null;
                          const episodeOptions = (selSeasonData?.episodes || []).sort((a, b) => a.epNum - b.epNum);
                          if (episodeOptions.length === 0) {
                            return <option value="" disabled>{r.tmdb_ep_name || '-'}</option>;
                          }
                          return episodeOptions.map((ep) => (
                            <option key={ep.epNum} value={ep.epNum}>
                              E{ep.epNum} {ep.name}{ep.name_cn ? ` / ${ep.name_cn}` : ''}
                            </option>
                          ));
                        })()}
                      </select>
                    </div>
                    <div className="ml-auto">
                      {r.matched ? (
                        <span className="bg-primary/10 text-primary text-[9px] px-2 py-0.5 rounded-full font-bold uppercase tracking-wider">Mapped</span>
                      ) : (
                        <span className="bg-secondary/10 text-secondary text-[9px] px-2 py-0.5 rounded-full font-bold uppercase tracking-wider">Pending</span>
                      )}
                    </div>
                  </div>
                </div>
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
              return (
                <div key={i} className="bg-surface-light dark:bg-surface-dark border border-amber-500/20 dark:border-amber-500/20 rounded-xl overflow-hidden shadow-sm transition-all hover:shadow-md group">
                  {/* Top row: file name + badges */}
                  <div className="px-4 py-3 border-b border-slate-50 dark:border-white/5">
                    <div className="flex items-center justify-between gap-4">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-3">
                          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-amber-400 shrink-0">
                            <path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z" />
                            <polyline points="14 2 14 8 20 8" />
                          </svg>
                          <h4 className="font-mono text-xs font-bold text-slate-700 dark:text-slate-200 truncate">{r.file_name}</h4>
                        </div>
                      </div>
                      <div className="flex items-center gap-4 shrink-0">
                        <div className="flex items-center gap-1.5 px-2 py-1 bg-amber-50 dark:bg-amber-500/5 rounded-md">
                          <span className="text-[10px] text-amber-500 font-bold uppercase tracking-tighter">SP</span>
                          <span className="text-xs font-semibold text-slate-600 dark:text-slate-300">{r.show_name}</span>
                        </div>
                        <div className="flex items-center gap-1.5 px-2 py-1 bg-slate-50 dark:bg-white/5 rounded-md">
                          <span className="text-[10px] text-slate-400 font-bold uppercase tracking-tighter">S/E</span>
                          <span className="text-xs font-semibold text-slate-600 dark:text-slate-300">{r.src_season} / {r.src_episode}</span>
                        </div>
                      </div>
                    </div>
                  </div>
                  {/* Bottom row: mapping controls (all default empty — user selects manually) */}
                  <div className="px-4 py-2.5 bg-slate-50/30 dark:bg-white/[0.02] flex flex-wrap items-center gap-x-6 gap-y-2">
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] text-slate-400 font-bold uppercase tracking-tighter">BGM Entry</span>
                      <select
                        className="text-[11px] py-0.5 px-1 bg-transparent border-slate-200 dark:border-white/10 rounded font-medium max-w-[100px] truncate focus:ring-1 focus:ring-primary/30 cursor-pointer"
                        value={currentEntryId || ''}
                        onChange={(e) => handleBgmEntryChange(i, e.target.value)}
                      >
                        {!currentEntryId && <option value="" disabled>-</option>}
                        {bgmEntryOptions.map((opt) => (
                          <option key={opt.id} value={opt.id}>{opt.name}</option>
                        ))}
                      </select>
                      <div className="flex items-center gap-1 ml-1">
                        <span className="text-[10px] text-slate-400 font-bold uppercase tracking-tighter">#</span>
                        <span className="text-[11px] font-mono text-slate-500">{r.bgm_sort ?? '-'}</span>
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] text-slate-400 font-bold uppercase tracking-tighter">BGM Name</span>
                      <select
                        className="text-[11px] py-0.5 px-1 bg-transparent border-slate-200 dark:border-white/10 rounded font-medium max-w-[220px] truncate focus:ring-1 focus:ring-primary/30 cursor-pointer"
                        value={r.bgm_sort ?? ''}
                        onChange={(e) => handleBgmEpChange(i, currentEntryId, e.target.value)}
                        title={`${r.bgm_ep_name}${r.bgm_ep_name_cn ? ` / ${r.bgm_ep_name_cn}` : ''}`}
                      >
                        {currentEps.length === 0 && (
                          <option value="" disabled>{r.bgm_ep_name || '-'}</option>
                        )}
                        {currentEps.map((ep) => (
                          <option key={ep.sort} value={ep.sort}>
                            E{ep.sort} {ep.name}{ep.name_cn ? ` / ${ep.name_cn}` : ''}
                          </option>
                        ))}
                      </select>
                    </div>
                    {/* SP cards: TMDB S aggregates ALL TMDB seasons from all shows */}
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] text-slate-400 font-bold uppercase tracking-tighter">TMDB S</span>
                      <select
                        className="text-[11px] py-0.5 px-1 bg-transparent border-slate-200 dark:border-white/10 rounded font-medium max-w-[160px] truncate focus:ring-1 focus:ring-primary/30 cursor-pointer"
                        value={(() => {
                          const ov = overrides[i];
                          if (ov?.tmdbShowId && ov.tmdbSeason != null) return `${ov.tmdbShowId}:${ov.tmdbSeason}`;
                          return r.tmdb_season ?? '';
                        })()}
                        onChange={(e) => handleTmdbSeasonChange(i, r.show_name, e.target.value)}
                      >
                        {r.tmdb_season == null && <option value="" disabled>-</option>}
                        {(() => {
                          const opts: { value: string; label: string }[] = [];
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
                              opts.push({
                                value: `${showTmdbId}:${skey}`,
                                label: `${showLabel}  ${sdata.name || `Season ${skey}`}`,
                              });
                            }
                          }
                          return opts.map((opt) => (
                            <option key={opt.value} value={opt.value}>{opt.label}</option>
                          ));
                        })()}
                      </select>
                    </div>
                    {/* SP cards: TMDB Ep looks up from the override's tmdbShowId */}
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] text-slate-400 font-bold uppercase tracking-tighter">TMDB Ep</span>
                      <select
                        className="text-[11px] py-0.5 px-1 bg-transparent border-slate-200 dark:border-white/10 rounded font-medium max-w-[220px] truncate focus:ring-1 focus:ring-primary/30 cursor-pointer"
                        value={r.tmdb_ep ?? ''}
                        onChange={(e) => handleTmdbEpChange(i, e.target.value)}
                        title={r.tmdb_ep_name}
                      >
                        {(() => {
                          const ov = overrides[i];
                          const lookupTmdbId = ov?.tmdbShowId ?? searchResults[r.show_name]?.tmdb?.id;
                          const lookupSeasons: Record<string, TmdbSeason> =
                            (lookupTmdbId && episodeData.tmdb?.[String(lookupTmdbId)]) || {};
                          const selSeasonKey = r.tmdb_season != null ? String(r.tmdb_season) : '';
                          const selSeasonData = selSeasonKey ? lookupSeasons[selSeasonKey] : null;
                          const episodeOptions = (selSeasonData?.episodes || []).sort((a, b) => a.epNum - b.epNum);
                          if (episodeOptions.length === 0) {
                            return <option value="" disabled>{r.tmdb_ep_name || '-'}</option>;
                          }
                          return episodeOptions.map((ep) => (
                            <option key={ep.epNum} value={ep.epNum}>
                              E{ep.epNum} {ep.name}{ep.name_cn ? ` / ${ep.name_cn}` : ''}
                            </option>
                          ));
                        })()}
                      </select>
                    </div>
                    <div className="ml-auto">
                      {r.matched ? (
                        <span className="bg-primary/10 text-primary text-[9px] px-2 py-0.5 rounded-full font-bold uppercase tracking-wider">Mapped</span>
                      ) : (
                        <span className="bg-amber-500/10 text-amber-500 text-[9px] px-2 py-0.5 rounded-full font-bold uppercase tracking-wider">Select</span>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
