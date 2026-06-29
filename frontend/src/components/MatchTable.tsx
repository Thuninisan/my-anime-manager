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

  // ── Initial auto-computed rows ──
  const initialRows = useMemo(() => computeMatches(data), [data]);

  // ── Per-row overrides: rowIndex → { bgmEntryId, bgmEpSort } ──
  const [overrides, setOverrides] = useState<
    Record<number, { bgmEntryId: number; bgmEpSort: number }>
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

      if (!ovEp) return r;

      // Re-compute TMDB match against the overridden BGM episode name
      const tmdbId = searchResults[r.show_name]?.tmdb?.id;
      const tmdbSeasons: Record<string, TmdbSeason> =
        (tmdbId && episodeData.tmdb?.[String(tmdbId)]) || {};
      const tmdbMatch = fuzzyMatchTmdb(
        ovEp.name,
        ovEp.name_cn || "",
        tmdbSeasons,
      );

      return {
        ...r,
        bgm_entry: ovEntry?.name || `ID ${ov.bgmEntryId}`,
        bgm_entry_id: ov.bgmEntryId,
        bgm_sort: ovEp.sort,
        bgm_ep_name: ovEp.name,
        bgm_ep_name_cn: ovEp.name_cn || '',
        bgm_ep_id: ovEp.id,
        tmdb_season: tmdbMatch?.season ?? null,
        tmdb_ep: tmdbMatch?.epNum ?? null,
        tmdb_ep_name: tmdbMatch?.name || '-',
        matched: tmdbMatch !== null,
      };
    });
  }, [initialRows, overrides, bgmEntryOptions, searchResults, episodeData]);

  // ── Split rows into movie / TV tables (preserve original indices for overrides) ──
  const movieRows = useMemo(
    () => rows.map((r, i) => ({ ...r, _idx: i })).filter((r) => r.media_type === "movie"),
    [rows],
  );
  const tvRows = useMemo(
    () => rows.map((r, i) => ({ ...r, _idx: i })).filter((r) => r.media_type !== "movie"),
    [rows],
  );

  return (
    <div className="max-w-full mx-auto mt-4 space-y-6">
      {/* ── Movie Table ── */}
      {movieRows.length > 0 && (
        <div className="glass-card rounded-xl p-4 overflow-auto max-h-[50vh]">
          <h3 className="text-sm font-medium text-muted-foreground mb-2">
            映画 ({movieRows.length} ファイル)
          </h3>
          <table className="w-full text-xs border-collapse">
            <thead className="sticky top-0 bg-card z-10">
              <tr className="border-b border-border text-muted-foreground">
                <th className="text-left p-1.5 whitespace-nowrap">File</th>
                <th className="text-left p-1.5 whitespace-nowrap">Show</th>
                <th className="text-left p-1.5 whitespace-nowrap">BGM Entry</th>
                <th className="text-left p-1.5">TMDB Name</th>
              </tr>
            </thead>
            <tbody>
              {movieRows.map((r) => {
                const i = (r as any)._idx as number;
                const currentEntryId = r.bgm_entry_id ?? 0;
                return (
                  <tr
                    key={i}
                    className={`border-b border-border/50 ${
                      r.matched ? '' : 'bg-amber-500/5'
                    }`}
                  >
                    <td className="p-1.5 font-mono whitespace-nowrap">{r.file_name}</td>
                    <td className="p-1.5 whitespace-nowrap text-muted-foreground">{r.show_name}</td>
                    <td className="p-1.5 max-w-[200px]">
                      <select
                        className="bg-transparent text-xs w-full truncate border border-border/50 rounded px-1 py-0.5 outline-none focus:ring-1 focus:ring-ring cursor-pointer"
                        value={currentEntryId || ''}
                        onChange={(e) => {
                          // Movie override: just update BGM entry (no episode selection)
                          const entryId = Number(e.target.value);
                          setOverrides((prev) => ({
                            ...prev,
                            [i]: { bgmEntryId: entryId, bgmEpSort: 0 },
                          }));
                        }}
                      >
                        {!currentEntryId && (
                          <option value="" disabled>-</option>
                        )}
                        {bgmEntryOptions.map((opt) => (
                          <option key={opt.id} value={opt.id}>
                            {opt.name}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className={`p-1.5 max-w-[250px] truncate ${r.matched ? '' : 'text-muted-foreground'}`}>
                      {r.tmdb_ep_name}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* ── TV Table ── */}
      {tvRows.length > 0 && (
        <div className="glass-card rounded-xl p-4 overflow-auto max-h-[50vh]">
          <h3 className="text-sm font-medium text-muted-foreground mb-2">
            TV / シリーズ ({tvRows.length} ファイル)
          </h3>
          <table className="w-full text-xs border-collapse">
            <thead className="sticky top-0 bg-card z-10">
              <tr className="border-b border-border text-muted-foreground">
                <th className="text-left p-1.5 whitespace-nowrap">File</th>
                <th className="text-left p-1.5 whitespace-nowrap">Show</th>
                <th className="text-center p-1.5 w-10">S</th>
                <th className="text-center p-1.5 w-10">E</th>
                <th className="text-left p-1.5 whitespace-nowrap">BGM Entry</th>
                <th className="text-center p-1.5 w-10">BGM#</th>
                <th className="text-left p-1.5">BGM Name</th>
                <th className="text-center p-1.5 w-10">T S</th>
                <th className="text-center p-1.5 w-10">T E</th>
                <th className="text-left p-1.5">TMDB Name</th>
              </tr>
            </thead>
            <tbody>
              {tvRows.map((r) => {
                const i = (r as any)._idx as number;
                const currentEps = r.bgm_entry_id
                  ? getBgmEpisodes(r.bgm_entry_id)
                  : [];
                const currentEntryId = r.bgm_entry_id ?? 0;

                return (
                  <tr
                    key={i}
                    className={`border-b border-border/50 ${
                      r.matched ? '' : 'bg-amber-500/5'
                    }`}
                  >
                    <td className="p-1.5 font-mono whitespace-nowrap">{r.file_name}</td>
                    <td className="p-1.5 whitespace-nowrap text-muted-foreground">{r.show_name}</td>
                    <td className="p-1.5 text-center">{r.src_season}</td>
                    <td className="p-1.5 text-center">{r.src_episode}</td>
                    {/* ── BGM Entry dropdown ── */}
                    <td className="p-1.5 max-w-[160px]">
                      <select
                        className="bg-transparent text-xs w-full truncate border border-border/50 rounded px-1 py-0.5 outline-none focus:ring-1 focus:ring-ring cursor-pointer"
                        value={currentEntryId || ''}
                        onChange={(e) => handleBgmEntryChange(i, e.target.value)}
                      >
                        {!currentEntryId && (
                          <option value="" disabled>
                            -
                          </option>
                        )}
                        {bgmEntryOptions.map((opt) => (
                          <option key={opt.id} value={opt.id}>
                            {opt.name}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="p-1.5 text-center">{r.bgm_sort ?? '-'}</td>
                    {/* ── BGM Name dropdown ── */}
                    <td className="p-1.5 max-w-[220px]">
                      <select
                        className="bg-transparent text-xs w-full truncate border border-border/50 rounded px-1 py-0.5 outline-none focus:ring-1 focus:ring-ring cursor-pointer"
                        value={r.bgm_sort ?? ''}
                        onChange={(e) =>
                          handleBgmEpChange(i, currentEntryId, e.target.value)
                        }
                        title={`${r.bgm_ep_name}${r.bgm_ep_name_cn ? ` / ${r.bgm_ep_name_cn}` : ''}`}
                      >
                        {currentEps.length === 0 && (
                          <option value="" disabled>
                            {r.bgm_ep_name || '-'}
                          </option>
                        )}
                        {currentEps.map((ep) => (
                          <option key={ep.sort} value={ep.sort}>
                            E{ep.sort} {ep.name}
                            {ep.name_cn ? ` / ${ep.name_cn}` : ''}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className={`p-1.5 text-center ${r.matched ? 'text-emerald-400' : 'text-muted-foreground'}`}>
                      {r.tmdb_season ?? '-'}
                    </td>
                    <td className={`p-1.5 text-center ${r.matched ? 'text-emerald-400' : 'text-muted-foreground'}`}>
                      {r.tmdb_ep ?? '-'}
                    </td>
                    <td className={`p-1.5 max-w-[200px] truncate ${r.matched ? '' : 'text-muted-foreground'}`}>
                      {r.tmdb_ep_name}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
