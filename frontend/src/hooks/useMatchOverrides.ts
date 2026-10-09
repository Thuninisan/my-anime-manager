import { manualVideoFiles, candidateIds, recommendedId } from '@/lib/episodeAdapters';
import type { EpisodeCatalog, CatalogSeason, TmdbEpisodeRef } from '@/types/episode';
import type { TorrentPreviewResponse } from '@/types/preview';
import { createEpisodeMapping } from '@/lib/episodeAdapters';
/** Custom hook for MatchTable override state and row recomputation.
 *
 * Extracted from MatchTable.tsx.  Manages the per-row override record,
 * all dropdown change handlers, and the effective rows computation
 * (applies overrides on top of auto-computed matches).
 */

import { useState, useMemo, useCallback } from 'react';
import type { MatchRow, SearchEntry, BgmEpisode, BgmEntry, TmdbSeason } from '@/types/matchTable';
import { computeMatches, DuplicateEpisodeError, matchEpisodeTitles } from '@/lib/matchUtils';

// ── Override record shape ──
export interface MatchOverrides {
  bgmEntryId?: number;
  bgmEpSort?: number;
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
  data: TorrentPreviewResponse,
  searchResults: Record<string, SearchEntry>,
  episodeData: EpisodeCatalog,
): UseMatchOverridesReturn {
  // ── Build BGM entry dropdown options ──
  const bgmEntryOptions = useMemo(() => {
    const options: { id: number; name: string }[] = [];
    const seen = new Set<number>();

    for (const entry of Object.values(searchResults)) {
      if (recommendedId(entry, 'bangumi') && !seen.has(recommendedId(entry, 'bangumi')!)) {
        seen.add(recommendedId(entry, 'bangumi')!);
        options.push({
          id: recommendedId(entry, 'bangumi')!,
          name: entry.bangumi_display_name || entry.bangumi_display_name || `ID ${recommendedId(entry, 'bangumi')}`,
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

    const available = new Set(Object.values(searchResults).flatMap(entry => candidateIds(entry, 'bangumi')));
    return options.filter(option => available.has(option.id)).sort((a, b) => a.name.localeCompare(b.name));
  }, [searchResults, episodeData]);

  // ── Initial auto-computed rows ──
  const computed = useMemo(() => {
    try {
      const regularRows = computeMatches(data);
      const specials = manualVideoFiles(data.parsed_files);
      const spRows: MatchRow[] = specials.map(s => ({
        file_name: s.file_name, torrent_path: s.torrent_path || s.file_name, show_name: s.show_name || '-',
        mapping: createEpisodeMapping(s.parsed, null, null, null, null, data.episode_match_source ?? null),
        bgm_entry: '-', bgm_ep_name: '-', bgm_ep_name_cn: '', tmdb_ep_name: '-',
        matched: false, media_type: 'special',
      }));
      return { rows: [...regularRows, ...spRows], error: null };
    } catch (err) {
      return { rows: [] as MatchRow[], error: err instanceof DuplicateEpisodeError ? err.message
        : `匹配失败: ${err instanceof Error ? err.message : String(err)}` };
    }
  }, [data]);
  const initialRows = computed.rows;
  const matchError = computed.error;

  // ── Per-row overrides ──
  const [overridesByFile, setOverridesByFile] = useState<Record<string, MatchOverrides>>({});
  const rowKey = useCallback((row: MatchRow) => data.parsed_files
    .find(file => file.torrent_path === row.torrent_path)?.file_id || row.torrent_path,
  [data.parsed_files]);
  // MatchTable reports rows to its parent in an effect. Keep this projection
  // stable so a parent render does not trigger another rows notification.
  const overrides = useMemo(() => Object.fromEntries(initialRows.map((row, index) =>
    [index, overridesByFile[rowKey(row)] ?? {}])), [initialRows, overridesByFile, rowKey]);
  const setOverrides = (update: (previous: Record<number, MatchOverrides>) => Record<number, MatchOverrides>) => {
    setOverridesByFile(previous => {
      const indexed = Object.fromEntries(initialRows.map((row, index) => [index, previous[rowKey(row)] ?? {}]));
      const next = update(indexed);
      return { ...previous, ...Object.fromEntries(initialRows.map((row, index) => [rowKey(row), next[index]])) };
    });
  };

  // ── Get episodes for a specific BGM entry ──
  const getBgmEpisodes = (entryId: number): BgmEpisode[] => {
    const bgmData: Record<string, BgmEntry> = episodeData.bangumi || {};
    return bgmData[String(entryId)]?.episodes || [];
  };

  // ── Base override builder ──
  // Store only fields the user touched; auto values follow catalog/index changes.
  const buildBaseOverride = (
    rowIndex: number,
    existing: MatchOverrides | undefined,
  ): MatchOverrides => {
    void rowIndex;
    return { ...existing };
  };

  // ── Handlers ──

  const handleBgmEntryChange = (rowIndex: number, entryIdStr: string) => {
    const entryId = Number(entryIdStr);
    const eps = getBgmEpisodes(entryId);
    const firstEp = matchEpisodeTitles(initialRows[rowIndex]?.tmdb_ep_name || '', eps).selected;
    setOverrides((prev) => {
      const existing = prev[rowIndex];
      return {
        ...prev,
        [rowIndex]: {
          ...buildBaseOverride(rowIndex, existing),
          bgmEntryId: entryId,
          bgmEpSort: firstEp?.episode_absolute ?? undefined,
          bgmEpId: firstEp?.episode_id,
        },
      };
    });
  };

  const handleBgmEpChange = (rowIndex: number, entryId: number, epIdStr: string) => {
    const epId = Number(epIdStr);
    const eps = getBgmEpisodes(entryId);
    const ep = eps.find(e => e.episode_id === epId);
    setOverrides((prev) => {
      const existing = prev[rowIndex];
      return {
        ...prev,
        [rowIndex]: {
          ...buildBaseOverride(rowIndex, existing),
          bgmEntryId: entryId,
          bgmEpSort: ep?.episode_absolute ?? 0,
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
      const sortedEps = [...(seasonData?.episodes || [])].sort((a, b) => a.episode_number - b.episode_number);
      const firstEp = sortedEps[0]?.episode_number;
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
    const tmdbSeriesId = recommendedId(searchResults[showName], 'tmdb');
    const tmdbSeasons: Record<string, TmdbSeason> =
      (tmdbSeriesId && episodeData.tmdb?.[String(tmdbSeriesId)]) || {};
    const seasonData = tmdbSeasons[String(season)];
    const sortedEps = [...(seasonData?.episodes || [])].sort((a, b) => a.episode_number - b.episode_number);
    const firstEp = sortedEps[0]?.episode_number;
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
          tmdbShowId: existing?.tmdbShowId ?? initialRows[rowIndex]?.mapping.tmdb.series_id ?? undefined,
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
      const sortedEps = [...(seasonData?.episodes || [])].sort((a, b) => a.episode_number - b.episode_number);
      const firstEp = sortedEps[0]?.episode_number;
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
      const tvdbSeriesId = base.tvdbShowId ?? initialRows[rowIndex]?.mapping.tvdb.series_id;
      const tvdbSeries = (tvdbSeriesId && episodeData?.tvdb?.[String(tvdbSeriesId)]) || null;
      const seasonData = tvdbSeries?.seasons?.[String(season)];
      const sortedEps = [...(seasonData?.episodes || [])].sort((a, b) => a.episode_number - b.episode_number);
      const firstEp = sortedEps[0]?.episode_number;
      return {
        ...prev,
        [rowIndex]: {
          ...base,
          tvdbShowId: base.tvdbShowId ?? initialRows[rowIndex]?.mapping.tvdb.series_id ?? undefined,
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
          tvdbShowId: existing?.tvdbShowId ?? initialRows[rowIndex]?.mapping.tvdb.series_id ?? undefined,
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
      const effectiveBgmId = ov.bgmEntryId ?? r.mapping.bangumi.subject_id ?? 0;
      const ovEntry = bgmEntryOptions.find((e) => e.id === effectiveBgmId);
      const eps = getBgmEpisodes(effectiveBgmId);
      const ovEp = ov.bgmEpId != null
        ? eps.find((e) => e.episode_id === ov.bgmEpId)
        : ov.bgmEpSort != null ? eps.find((e) => e.episode_absolute === ov.bgmEpSort)
          : eps.find((e) => e.episode_id === r.mapping.bangumi.episode_id);

      // TMDB ep name lookup (display only)
      let tmdbEpName = r.tmdb_ep_name;
      const effTmdbSeason = ov.tmdbSeason ?? r.mapping.tmdb.season_number;
      const effTmdbEp = ov.tmdbEp ?? r.mapping.tmdb.episode_number;
      if (effTmdbSeason != null && effTmdbEp != null) {
        const tmdbSeriesId = ov.tmdbShowId ?? r.mapping.tmdb.series_id;
        const tmdbSeasons: Record<string, CatalogSeason> =
          (tmdbSeriesId && episodeData.tmdb?.[String(tmdbSeriesId)]) || {};
        const sData = tmdbSeasons[String(effTmdbSeason)];
        const eData = sData?.episodes?.find((e) => e.episode_number === effTmdbEp);
        tmdbEpName = eData?.name || '-';
      }

      const resolve = (source: 'tmdb' | 'tvdb', seriesId: number | undefined,
        season: number | null, episode: number | null): TmdbEpisodeRef => {
        if (season == null && episode == null) return {
          series_id: null, episode_id: null, season_number: null, episode_number: null,
        };
        const seasons = source === 'tmdb' ? episodeData.tmdb[String(seriesId)]
          : episodeData.tvdb[String(seriesId)]?.seasons;
        const candidate = season != null && episode != null
          ? seasons?.[String(season)]?.episodes.find(e => e.episode_number === episode) : undefined;
        return { series_id: seriesId ?? null, episode_id: candidate?.episode_id ?? null,
          season_number: season, episode_number: episode };
      };
      const mapping = {
        ...r.mapping,
        bangumi: { subject_id: effectiveBgmId || null,
          episode_id: r.media_type === 'movie' ? null : ovEp?.episode_id ?? null,
          episode_number: r.media_type === 'movie' ? null : ovEp?.episode_number ?? null,
          episode_absolute: r.media_type === 'movie' ? null : ovEp?.episode_absolute ?? null },
        tmdb: resolve('tmdb', ov.tmdbShowId ?? r.mapping.tmdb.series_id ?? undefined, effTmdbSeason, effTmdbEp),
        tvdb: resolve('tvdb', ov.tvdbShowId ?? r.mapping.tvdb.series_id ?? undefined,
          ov.tvdbSeason ?? r.mapping.tvdb.season_number, ov.tvdbEp ?? r.mapping.tvdb.episode_number),
      };
      const contexts = Object.values(searchResults).filter(context =>
        (['tmdb', 'tvdb', 'bangumi'] as const).every(provider => {
          const id = provider === 'bangumi' ? mapping.bangumi.subject_id : mapping[provider].series_id;
          return id == null || candidateIds(context, provider).includes(id);
        }));
      const entry = searchResults[r.show_name] ?? (contexts.length === 1 ? contexts[0] : undefined);
      const allowed = (provider: 'tmdb' | 'tvdb' | 'bangumi', id: number | null) => id == null || candidateIds(entry, provider).includes(id);
      const valid = allowed('bangumi', mapping.bangumi.subject_id) && allowed('tmdb', mapping.tmdb.series_id) && allowed('tvdb', mapping.tvdb.series_id);
      const anchor = mapping[mapping.match_source ?? 'tmdb'];
      const source = mapping.match_source ?? 'tmdb';
      const anchorSeasons = source === 'tmdb' ? episodeData.tmdb[String(anchor.series_id)] : episodeData.tvdb[String(anchor.series_id)]?.seasons;
      const anchorExists = anchorSeasons?.[String(anchor.season_number)]?.episodes.some(ep => ep.episode_number === anchor.episode_number);
      const matched = valid && (r.media_type === 'movie' ? !!(effectiveBgmId && r.tmdb_movie_id)
        : !!(ovEp && anchorExists)) && ov.manualMatched !== false;
      return {
        ...r,
        match_status: r.match_status ? { ...r.match_status,
          ...(ov.bgmEntryId != null || ov.bgmEpId != null ? { bangumi: 'manual' as const } : {}),
          ...(ov.tmdbSeason != null || ov.tmdbEp != null ? { tmdb: 'manual' as const } : {}),
          ...(ov.tvdbSeason != null || ov.tvdbEp != null ? { tvdb: 'manual' as const } : {}),
        } : undefined,
        mapping,
        bgm_entry: ovEntry?.name || (effectiveBgmId ? `ID ${effectiveBgmId}` : '-'),
        bgm_ep_name: r.media_type === 'movie' ? ovEntry?.name || r.bgm_ep_name : ovEp?.name || r.bgm_ep_name,
        bgm_ep_name_cn: r.media_type === 'movie' ? '' : ovEp?.name_cn || r.bgm_ep_name_cn,
        tmdb_ep_name: tmdbEpName,
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
