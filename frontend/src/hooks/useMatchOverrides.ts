/** Custom hook for MatchTable override state and row recomputation.
 *
 * Extracted from MatchTable.tsx.  Manages the per-row override record,
 * all dropdown change handlers, and the effective rows computation
 * (applies overrides on top of auto-computed matches).
 */

import { useState, useMemo } from 'react';
import type { MatchRow, SearchEntry, BgmEpisode, BgmEntry, TmdbSeason } from '@/types/matchTable';
import { computeMatches, DuplicateEpisodeError } from '@/lib/matchUtils';

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

  // ── Base override builder ──
  // Returns a complete MatchOverrides for a row by layering:
  //   existing override > initialRow auto-matched values > sentinel defaults.
  // Handlers spread this and only change the field(s) the user actually
  // touched, so unrelated values are never reset to 0 / first-episode.
  const buildBaseOverride = (
    rowIndex: number,
    existing: MatchOverrides | undefined,
  ): MatchOverrides => {
    const ir = initialRows[rowIndex];
    const se = ir ? searchResults[ir.show_name] : undefined;
    const mapEntry = se?.map_entries?.find(
      (me: any) => me.bangumi_id === ir?.bgm_entry_id,
    );
    return {
      bgmEntryId: existing?.bgmEntryId ?? ir?.bgm_entry_id ?? 0,
      bgmEpSort: existing?.bgmEpSort ?? ir?.bgm_sort ?? 0,
      bgmEpId: existing?.bgmEpId ?? ir?.bgm_ep_id ?? undefined,
      tmdbSeason: existing?.tmdbSeason ?? ir?.tmdb_season ?? undefined,
      tmdbEp: existing?.tmdbEp ?? ir?.tmdb_ep ?? undefined,
      tmdbShowId: existing?.tmdbShowId ?? se?.tmdb?.id,
      tvdbShowId: existing?.tvdbShowId ?? mapEntry?.tvdb_id,
      tvdbSeason: existing?.tvdbSeason ?? ir?.tvdb_season ?? undefined,
      tvdbEp: existing?.tvdbEp ?? ir?.tvdb_ep ?? undefined,
    };
  };

  // ── Handlers ──

  const handleBgmEntryChange = (rowIndex: number, entryIdStr: string) => {
    const entryId = Number(entryIdStr);
    const eps = getBgmEpisodes(entryId);
    const firstEp = eps[0];
    setOverrides((prev) => {
      const existing = prev[rowIndex];
      return {
        ...prev,
        [rowIndex]: {
          ...buildBaseOverride(rowIndex, existing),
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
      const existing = prev[rowIndex];
      return {
        ...prev,
        [rowIndex]: {
          ...buildBaseOverride(rowIndex, existing),
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
        return {
          ...prev,
          [rowIndex]: {
            ...buildBaseOverride(rowIndex, existing),
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
      return {
        ...prev,
        [rowIndex]: {
          ...buildBaseOverride(rowIndex, existing),
          tmdbSeason: season,
          tmdbEp: firstEp,
        },
      };
    });
  };

  const handleToggleMatched = (rowIndex: number, currentMatched: boolean) => {
    setOverrides((prev) => {
      const existing = prev[rowIndex];
      return {
        ...prev,
        [rowIndex]: {
          ...buildBaseOverride(rowIndex, existing),
          manualMatched: !currentMatched,
        },
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
          ...buildBaseOverride(rowIndex, existing),
          tmdbEp: ep,
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
        const existing = prev[rowIndex];
        return {
          ...prev,
          [rowIndex]: {
            ...buildBaseOverride(rowIndex, existing),
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
      const existing = prev[rowIndex];
      const base = buildBaseOverride(rowIndex, existing);
      const tvdbId = base.tvdbShowId;
      const tvdbSeries = (tvdbId && episodeData?.tvdb?.[String(tvdbId)]) || null;
      const seasonData = tvdbSeries?.seasons?.[String(season)];
      const sortedEps = [...(seasonData?.episodes || [])].sort((a: any, b: any) => a.epNum - b.epNum);
      const firstEp = sortedEps[0]?.epNum;
      return {
        ...prev,
        [rowIndex]: {
          ...base,
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
          ...buildBaseOverride(rowIndex, existing),
          tvdbEp: ep,
        },
      };
    });
  };

  // ── Effective rows (pure merge: initialRow + override overlay) ──
  const rows = useMemo(() => {
    return initialRows.map((r, i) => {
      const ov = overrides[i];
      if (!ov) return r;

      // BGM name lookup
      const ovEntry = bgmEntryOptions.find((e) => e.id === ov.bgmEntryId);
      const eps = getBgmEpisodes(ov.bgmEntryId);
      const ovEp = ov.bgmEpId != null
        ? eps.find((e) => e.id === ov.bgmEpId)
        : eps.find((e) => e.sort === ov.bgmEpSort);

      // TMDB ep name lookup (display only)
      let tmdbEpName = r.tmdb_ep_name;
      const effTmdbSeason = ov.tmdbSeason ?? r.tmdb_season;
      const effTmdbEp = ov.tmdbEp ?? r.tmdb_ep;
      if (effTmdbSeason != null && effTmdbEp != null) {
        const tmdbId = ov.tmdbShowId ?? searchResults[r.show_name]?.tmdb?.id;
        const tmdbSeasons: Record<string, any> =
          (tmdbId && episodeData.tmdb?.[String(tmdbId)]) || {};
        const sData = tmdbSeasons[String(effTmdbSeason)];
        const eData = sData?.episodes?.find((e: any) => e.epNum === effTmdbEp);
        tmdbEpName = eData?.name || '-';
      }

      let matched = r.matched;
      if (ov.manualMatched !== undefined) matched = ov.manualMatched;

      // Movie rows: only BGM entry changes, no season/ep
      if (r.media_type === "movie") {
        return {
          ...r,
          bgm_entry: ovEntry?.name || `ID ${ov.bgmEntryId}`,
          bgm_entry_id: ov.bgmEntryId,
          bgm_ep_name: ovEntry?.name || r.bgm_ep_name,
          bgm_ep_name_cn: '',
          bgm_ep_id: null,
          bgm_sort: null,
          matched,
        };
      }

      return {
        ...r,
        bgm_entry: ovEntry?.name || `ID ${ov.bgmEntryId}`,
        bgm_entry_id: ov.bgmEntryId,
        bgm_sort: ovEp?.sort ?? r.bgm_sort,
        bgm_ep_name: ovEp?.name || r.bgm_ep_name,
        bgm_ep_name_cn: ovEp?.name_cn || r.bgm_ep_name_cn,
        bgm_ep_id: ovEp?.id ?? r.bgm_ep_id,
        tmdb_season: effTmdbSeason,
        tmdb_ep: effTmdbEp,
        tmdb_ep_name: tmdbEpName,
        tvdb_season: ov.tvdbSeason ?? r.tvdb_season,
        tvdb_ep: ov.tvdbEp ?? r.tvdb_ep,
        matched,
      };
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
