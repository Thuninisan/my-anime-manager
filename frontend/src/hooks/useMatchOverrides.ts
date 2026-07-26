/** Custom hook for MatchTable override state and row recomputation.
 *
 * Extracted from MatchTable.tsx.  Manages the per-row override record,
 * all dropdown change handlers, and the effective rows computation
 * (applies overrides on top of auto-computed matches).
 */

import { useState, useMemo } from 'react';
import type { MatchRow, SearchEntry, BgmEpisode, BgmEntry, TmdbSeason } from '@/types/matchTable';
import { fuzzyMatchTmdb, mergeAllTmdbSeasons, computeMatches, DuplicateEpisodeError } from '@/lib/matchUtils';

// ── Override record shape ──
export interface MatchOverrides {
  bgmEntryId: number;
  bgmEpSort: number;
  bgmEpId?: number;
  tmdbSeason?: number;
  tmdbEp?: number;
  tmdbShowId?: number;
  tvdbShowId?: number;
  tvdbSeason?: number;
  tvdbEp?: number;
  manualMatched?: boolean;
}

// ── Hook return type ──
export interface UseMatchOverridesReturn {
  overrides: Record<number, MatchOverrides>;
  rows: MatchRow[];
  movieRows: (MatchRow & { _idx: number })[];
  tvRows: (MatchRow & { _idx: number })[];
  spRows: (MatchRow & { _idx: number })[];
  bgmEntryOptions: { id: number; name: string }[];
  matchError: string | null;
  getBgmEpisodes: (entryId: number) => BgmEpisode[];
  handleBgmEntryChange: (rowIndex: number, entryIdStr: string) => void;
  handleBgmEpChange: (rowIndex: number, entryId: number, epIdStr: string) => void;
  handleTmdbSeasonChange: (rowIndex: number, showName: string, seasonStr: string) => void;
  handleTmdbEpChange: (rowIndex: number, epStr: string) => void;
  handleTvdbSeasonChange: (rowIndex: number, seasonStr: string) => void;
  handleTvdbEpChange: (rowIndex: number, epStr: string) => void;
  handleToggleMatched: (rowIndex: number, currentMatched: boolean) => void;
}

export function useMatchOverrides(
  data: any,
  searchResults: Record<string, SearchEntry>,
  episodeData: any,
): UseMatchOverridesReturn {
  // ── Build BGM entry dropdown options ──
  const bgmEntryOptions = useMemo(() => {
    const options: { id: number; name: string }[] = [];
    const seen = new Set<number>();

    for (const entry of Object.values(searchResults)) {
      if (entry.bangumi?.id && !seen.has(entry.bangumi.id)) {
        seen.add(entry.bangumi.id);
        options.push({
          id: entry.bangumi.id,
          name: entry.bangumi.name_cn || entry.bangumi.name || `ID ${entry.bangumi.id}`,
        });
      }
    }

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
  const [matchError, setMatchError] = useState<string | null>(null);

  const initialRows = useMemo(() => {
    try {
      const regularRows = computeMatches(data);
      const specials: any[] = data.specials || [];
      const spRows: MatchRow[] = specials.map((s: any) => ({
        file_name: s.file_name,
        torrent_path: s.torrent_path || s.file_name,
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
        tvdb_season: null,
        tvdb_ep: null,
        matched: false,
        media_type: "special" as any,
      }));
      setMatchError(null);
      return [...regularRows, ...spRows];
    } catch (err: any) {
      if (err instanceof DuplicateEpisodeError) {
        setMatchError(err.message);
      } else {
        setMatchError(`匹配失败: ${err.message || err}`);
      }
      return [] as MatchRow[];
    }
  }, [data]);

  // ── Per-row overrides ──
  const [overrides, setOverrides] = useState<Record<number, MatchOverrides>>({});

  // ── Get episodes for a specific BGM entry ──
  const getBgmEpisodes = (entryId: number): BgmEpisode[] => {
    const bgmData: Record<string, BgmEntry> = episodeData.bangumi || {};
    return bgmData[String(entryId)]?.episodes || [];
  };

  // ── Handlers ──

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
          bgmEpId: firstEp?.id,
        },
      };
    });
  };

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
          bgmEpId: epId,
        },
      };
    });
  };

  const handleTmdbSeasonChange = (rowIndex: number, showName: string, seasonStr: string) => {
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
        const initialRow = initialRows[rowIndex];
        return {
          ...prev,
          [rowIndex]: {
            bgmEntryId: existing?.bgmEntryId ?? initialRow?.bgm_entry_id ?? 0,
            bgmEpSort: existing?.bgmEpSort ?? initialRow?.bgm_sort ?? 0,
            bgmEpId: existing?.bgmEpId ?? initialRow?.bgm_ep_id ?? undefined,
            tmdbSeason: season,
            tmdbEp: firstEp,
            tmdbShowId: tmdbShowId,
          },
        };
      });
      return;
    }

    const season = Number(seasonStr);
    const tmdbId = searchResults[showName]?.tmdb?.id;
    const tmdbSeasons: Record<string, TmdbSeason> =
      (tmdbId && episodeData.tmdb?.[String(tmdbId)]) || {};
    const seasonData = tmdbSeasons[String(season)];
    const sortedEps = [...(seasonData?.episodes || [])].sort((a, b) => a.epNum - b.epNum);
    const firstEp = sortedEps[0]?.epNum;
    setOverrides((prev) => {
      const existing = prev[rowIndex];
      const initialRow = initialRows[rowIndex];
      return {
        ...prev,
        [rowIndex]: {
          bgmEntryId: existing?.bgmEntryId ?? initialRow?.bgm_entry_id ?? 0,
          bgmEpSort: existing?.bgmEpSort ?? initialRow?.bgm_sort ?? 0,
          bgmEpId: existing?.bgmEpId ?? initialRow?.bgm_ep_id ?? undefined,
          tmdbSeason: season,
          tmdbEp: firstEp,
        },
      };
    });
  };

  const handleToggleMatched = (rowIndex: number, currentMatched: boolean) => {
    setOverrides((prev) => {
      const existing = prev[rowIndex] || { bgmEntryId: 0, bgmEpSort: 0 };
      return {
        ...prev,
        [rowIndex]: { ...existing, manualMatched: !currentMatched },
      };
    });
  };

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
          tmdbShowId: existing?.tmdbShowId,
        },
      };
    });
  };

  const handleTvdbSeasonChange = (rowIndex: number, seasonStr: string) => {
    if (seasonStr.includes(":")) {
      const [tvdbIdStr, seasonStr2] = seasonStr.split(":");
      const tvdbShowId = Number(tvdbIdStr);
      const season = Number(seasonStr2);
      const tvdbSeries = episodeData?.tvdb?.[String(tvdbShowId)];
      const seasonData = tvdbSeries?.seasons?.[String(season)];
      const sortedEps = [...(seasonData?.episodes || [])].sort((a: any, b: any) => a.epNum - b.epNum);
      const firstEp = sortedEps[0]?.epNum;
      setOverrides((prev) => {
        const existing = prev[rowIndex] || {};
        return {
          ...prev,
          [rowIndex]: {
            bgmEntryId: existing?.bgmEntryId ?? 0,
            bgmEpSort: existing?.bgmEpSort ?? 0,
            tvdbShowId: tvdbShowId,
            tvdbSeason: season,
            tvdbEp: firstEp,
          },
        };
      });
      return;
    }

    const season = Number(seasonStr);
    setOverrides((prev) => {
      const existing = prev[rowIndex] || {};
      const tvdbId = existing.tvdbShowId;
      const tvdbSeries = (tvdbId && episodeData?.tvdb?.[String(tvdbId)]) || null;
      const seasonData = tvdbSeries?.seasons?.[String(season)];
      const sortedEps = [...(seasonData?.episodes || [])].sort((a: any, b: any) => a.epNum - b.epNum);
      const firstEp = sortedEps[0]?.epNum;
      return {
        ...prev,
        [rowIndex]: {
          bgmEntryId: existing?.bgmEntryId ?? 0,
          bgmEpSort: existing?.bgmEpSort ?? 0,
          tvdbShowId: tvdbId,
          tvdbSeason: season,
          tvdbEp: firstEp,
        },
      };
    });
  };

  const handleTvdbEpChange = (rowIndex: number, epStr: string) => {
    const ep = Number(epStr);
    setOverrides((prev) => {
      const existing = prev[rowIndex];
      return {
        ...prev,
        [rowIndex]: {
          bgmEntryId: existing?.bgmEntryId ?? 0,
          bgmEpSort: existing?.bgmEpSort ?? 0,
          tvdbShowId: existing?.tvdbShowId,
          tvdbSeason: existing?.tvdbSeason ?? 0,
          tvdbEp: ep,
        },
      };
    });
  };

  // ── Effective rows (apply overrides + re-compute TMDB match) ──
  const rows = useMemo(() => {
    return initialRows.map((r, i) => {
      const ov = overrides[i];
      if (!ov) return r;

      const applyManualMatched = (row: MatchRow, _computedMatched: boolean): MatchRow => {
        if (ov.manualMatched !== undefined) {
          return { ...row, matched: ov.manualMatched };
        }
        return row;
      };

      const ovEntry = bgmEntryOptions.find((e) => e.id === ov.bgmEntryId);
      const eps = getBgmEpisodes(ov.bgmEntryId);
      const ovEp = ov.bgmEpId != null
        ? eps.find((e) => e.id === ov.bgmEpId)
        : eps.find((e) => e.sort === ov.bgmEpSort);

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

      const resolveTmdbId = ov.tmdbShowId ?? searchResults[r.show_name]?.tmdb?.id;
      const resolveAutoSeasons: Record<string, TmdbSeason> =
        (resolveTmdbId && episodeData.tmdb?.[String(resolveTmdbId)]) || {};
      const tmdbSeasons: Record<string, TmdbSeason> =
        Object.keys(resolveAutoSeasons).length > 0
          ? resolveAutoSeasons
          : mergeAllTmdbSeasons(episodeData);

      if (!ovEp) {
        let tvdbSeasonOv = r.tvdb_season;
        let tvdbEpOv = r.tvdb_ep;
        if (ov.tvdbSeason != null) {
          tvdbSeasonOv = ov.tvdbSeason;
          tvdbEpOv = ov.tvdbEp ?? null;
        }
        if (ov.tmdbSeason != null && ov.tmdbEp != null) {
          const sData = tmdbSeasons[String(ov.tmdbSeason)];
          const eData = sData?.episodes?.find(e => e.epNum === ov.tmdbEp);
          return applyManualMatched({
            ...r,
            tmdb_season: ov.tmdbSeason,
            tmdb_ep: ov.tmdbEp,
            tmdb_ep_name: eData?.name || '-',
            tvdb_season: tvdbSeasonOv,
            tvdb_ep: tvdbEpOv,
            matched: true,
          }, true);
        }
        if (ov.tvdbSeason != null) {
          return applyManualMatched({
            ...r,
            tvdb_season: tvdbSeasonOv,
            tvdb_ep: tvdbEpOv,
          }, r.matched);
        }
        return applyManualMatched(r, r.matched);
      }

      const tmdbMatch = fuzzyMatchTmdb(
        ovEp.name,
        ovEp.name_cn || "",
        tmdbSeasons,
      );

      let finalSeason = tmdbMatch?.season ?? null;
      let finalEp = tmdbMatch?.epNum ?? null;
      let finalEpName = tmdbMatch?.name || '-';
      let finalMatched = tmdbMatch !== null;

      if (ov.tmdbSeason != null && ov.tmdbEp != null) {
        finalSeason = ov.tmdbSeason;
        finalEp = ov.tmdbEp;
        finalMatched = true;
        const sData = tmdbSeasons[String(ov.tmdbSeason)];
        const eData = sData?.episodes?.find(e => e.epNum === ov.tmdbEp);
        finalEpName = eData?.name || '-';
      }

      if (finalSeason == null && ov.tmdbSeason != null) {
        finalSeason = ov.tmdbSeason;
      }

      let finalTvdbSeason = r.tvdb_season;
      let finalTvdbEp = r.tvdb_ep;
      if (ov.tvdbSeason != null) {
        finalTvdbSeason = ov.tvdbSeason;
        finalTvdbEp = ov.tvdbEp ?? null;
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
        tvdb_season: finalTvdbSeason,
        tvdb_ep: finalTvdbEp,
        matched: finalMatched,
      }, finalMatched);
    });
  }, [initialRows, overrides, bgmEntryOptions, searchResults, episodeData]);

  // ── Split rows by media_type ──
  const movieRows = useMemo(
    () => rows.map((r, i) => ({ ...r, _idx: i })).filter((r) => r.media_type === "movie"),
    [rows],
  );
  const tvRows = useMemo(
    () => rows.map((r, i) => ({ ...r, _idx: i })).filter((r) => r.media_type !== "movie" && r.media_type !== "special"),
    [rows],
  );
  const spRows = useMemo(
    () => rows.map((r, i) => ({ ...r, _idx: i })).filter((r) => r.media_type === "special"),
    [rows],
  );

  return {
    overrides,
    rows,
    movieRows,
    tvRows,
    spRows,
    bgmEntryOptions,
    matchError,
    getBgmEpisodes,
    handleBgmEntryChange,
    handleBgmEpChange,
    handleTmdbSeasonChange,
    handleTmdbEpChange,
    handleTvdbSeasonChange,
    handleTvdbEpChange,
    handleToggleMatched,
  };
}
