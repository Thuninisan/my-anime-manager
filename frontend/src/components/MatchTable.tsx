/** Matching logic: parsed_files → search_results → episode_data → table.

   1. parsed_file.show_name → search_results[key]
   2. bangumi.id → episode_data.bangumi[id].episodes (sorted by sort)
   3. parsed_file.episode → positional index → bangumi episode
   4. bangumi ep .name → fuzzy match TMDB episodes across all seasons
   5. Return TMDB season + episode

   BGM Entry / BGM Name columns have dropdowns populated from
   search_results + episode_data so the user can override the
   auto-matched entry and episode.

   State management and matching logic live in:
     hooks/useMatchOverrides.ts   — overrides, handlers, rows computation
     hooks/useSubtitleMatching.ts — subtitle upload/delete/batch
     lib/matchUtils.ts            — pure matching functions + options builders
     types/matchTable.ts          — shared type definitions
*/

import { useEffect } from 'react';
import MappingCard from '@/components/Cards/MappingCard';
import type { MatchRow, TmdbSeason } from '@/types/matchTable';

import { useMatchOverrides } from '@/hooks/useMatchOverrides';
import { useSubtitleMatching } from '@/hooks/useSubtitleMatching';
import {
  buildTmdbSeasonOptions, buildTmdbEpOptions,
  buildTvdbSeasonOptions, buildTvdbEpOptions,
  buildSpSeasonOptions, mergeAllTmdbSeasons,
} from '@/lib/matchUtils';
import { showError } from '@/lib/toast';

// Re-export types for external consumers (TorrentPreview, MappingCard)
export type { MatchRow, BgmEpisode } from '@/types/matchTable';
export { computeMatches } from '@/lib/matchUtils';

export default function MatchTable({ data, onRowsComputed, onSubtitlesChange }: {
  data: any;
  onRowsComputed?: (rows: MatchRow[]) => void;
  onSubtitlesChange?: (subs: { originalFilename: string; storedFilename: string }[]) => void;
}) {
  const searchResults = data.search_results || {};
  const episodeData = data.episode_data || { tmdb: {}, bangumi: {} };
  const subtitles: string[] = data.subtitles || [];
  const torrentName: string = data.torrent_name || '';

  // ── Matching state + handlers ──
  const {
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
  } = useMatchOverrides(data, searchResults, episodeData);

  // ── Subtitle state ──
  const {
    uploadedSubtitles,
    hasMatchingSubtitle,
    isUploadedMatch,
    getUploadedStoredFilename,
    handleSubtitleUploaded,
    makeHandleSubtitleDeleted,
    batchFolderRef,
    batchProcessing,
    batchProgress,
    handleBatchFolderUpload,
  } = useSubtitleMatching(subtitles, torrentName, tvRows);

  // ── Notify parent ──
  useEffect(() => { onRowsComputed?.(rows); }, [rows, onRowsComputed]);
  useEffect(() => { onSubtitlesChange?.(uploadedSubtitles); }, [uploadedSubtitles, onSubtitlesChange]);

  // ── Show match errors via toast ──
  useEffect(() => {
    if (matchError) showError(matchError);
  }, [matchError]);

  // ── Shared subtitle callbacks ──
  const subProps = (fileName: string) => {
    const sf = getUploadedStoredFilename(fileName);
    return {
      hasSubtitle: hasMatchingSubtitle(fileName),
      isUploadedSubtitle: isUploadedMatch(fileName),
      onSubtitleDeleted: sf ? makeHandleSubtitleDeleted(sf) : undefined,
    };
  };

  return (
    <div className="space-y-10">
      {/* ── Movie Table ── */}
      {movieRows.length > 0 && (
        <div className="mb-10">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-primary">
                <rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18" />
                <line x1="7" y1="2" x2="7" y2="22" /><line x1="17" y1="2" x2="17" y2="22" />
                <line x1="2" y1="12" x2="22" y2="12" /><line x1="2" y1="7" x2="7" y2="7" />
                <line x1="2" y1="17" x2="7" y2="17" /><line x1="17" y1="7" x2="22" y2="7" />
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
                  key={i} row={r} rowIndex={i} variant="movie"
                  torrentName={torrentName}
                  onSubtitleUploaded={handleSubtitleUploaded}
                  {...subProps(r.file_name)}
                  bgmEntryOptions={bgmEntryOptions}
                  currentEps={currentEps} currentEntryId={currentEntryId}
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
              <input ref={batchFolderRef} type="file"
                // @ts-ignore
                webkitdirectory="" directory=""
                accept=".ass,.ssa,.srt,.sub,.idx,.vtt,.ttml,.sbv,.dfxp"
                className="hidden" onChange={handleBatchFolderUpload}
              />
              <button
                className="inline-flex items-center gap-1.5 bg-[#f09199]/10 text-[#f09199] text-[10px] px-3 py-1 rounded-full font-bold uppercase tracking-wider hover:bg-[#f09199]/25 transition-colors cursor-pointer disabled:opacity-50"
                title="批量上传字幕文件夹 — 自动按集数匹配"
                onClick={() => batchFolderRef.current?.click()}
                disabled={batchProcessing}
              >
                {batchProcessing ? (
                  <><div className="w-3 h-3 border-2 border-[#f09199]/30 border-t-[#f09199] rounded-full animate-spin" />匹配中...</>
                ) : '+SUB'}
              </button>
            </div>
          </div>
          {batchProgress && <p className="text-xs text-slate-500 mb-3 -mt-1">{batchProgress}</p>}
          <div className="space-y-3">
            {tvRows.map((r) => {
              const i = (r as any)._idx as number;
              const currentEps = r.bgm_entry_id ? getBgmEpisodes(r.bgm_entry_id) : [];
              const currentEntryId = r.bgm_entry_id ?? 0;

              // TMDB options
              const { seasons: tmdbSeasons, opts: tmdbSeasonOpts } =
                buildTmdbSeasonOptions(r.show_name, searchResults, episodeData);
              const tmdbEpOpts = buildTmdbEpOptions(r.tmdb_season, tmdbSeasons);

              // TVDB options
              const { seasons: tvdbSeasons, opts: tvdbSeasonOpts } =
                buildTvdbSeasonOptions(currentEntryId, r.show_name, searchResults, episodeData, overrides[i]?.tvdbShowId);
              const { opts: tvdbEpOpts, title: tvdbEpTitle } =
                buildTvdbEpOptions(r.tvdb_season, tvdbSeasons);

              return (
                <MappingCard
                  key={i} row={r} rowIndex={i} variant="tv"
                  torrentName={torrentName}
                  onSubtitleUploaded={handleSubtitleUploaded}
                  {...subProps(r.file_name)}
                  bgmEntryOptions={bgmEntryOptions}
                  currentEps={currentEps} currentEntryId={currentEntryId}
                  tmdbSeasonOptions={tmdbSeasonOpts} tmdbSeasonValue={r.tmdb_season ?? ''}
                  tmdbEpOptions={tmdbEpOpts} tmdbEpValue={r.tmdb_ep ?? ''} tmdbEpTitle={r.tmdb_ep_name}
                  tvdbSeasonOptions={tvdbSeasonOpts} tvdbSeasonValue={r.tvdb_season ?? ''}
                  tvdbEpOptions={tvdbEpOpts} tvdbEpValue={r.tvdb_ep ?? ''} tvdbEpTitle={tvdbEpTitle}
                  onBgmEntryChange={(v) => handleBgmEntryChange(i, v)}
                  onBgmEpChange={(v) => handleBgmEpChange(i, currentEntryId, v)}
                  onTmdbSeasonChange={(v) => handleTmdbSeasonChange(i, r.show_name, v)}
                  onTmdbEpChange={(v) => handleTmdbEpChange(i, v)}
                  onTvdbSeasonChange={(v) => handleTvdbSeasonChange(i, v)}
                  onTvdbEpChange={(v) => handleTvdbEpChange(i, v)}
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
              const ov = overrides[i];

              // SP TMDB options (aggregate all shows)
              const tmdbSeasonOpts = buildSpSeasonOptions(episodeData, searchResults, 'tmdb');
              const tmdbSeasonVal = ov?.tmdbShowId && ov.tmdbSeason != null
                ? `${ov.tmdbShowId}:${ov.tmdbSeason}` : (r.tmdb_season ?? '');

              const lookupTmdbId = ov?.tmdbShowId ?? searchResults[r.show_name]?.tmdb?.id;
              const lookupSeasons: Record<string, TmdbSeason> =
                (lookupTmdbId && episodeData.tmdb?.[String(lookupTmdbId)]) || {};
              const spTmdbSeasons: Record<string, TmdbSeason> =
                Object.keys(lookupSeasons).length > 0 ? lookupSeasons : mergeAllTmdbSeasons(episodeData);
              const tmdbEpOpts = buildTmdbEpOptions(r.tmdb_season, spTmdbSeasons);

              // SP TVDB options (aggregate all shows)
              const tvdbSeasonOpts = buildSpSeasonOptions(episodeData, searchResults, 'tvdb');
              const tvdbSeasonVal = ov?.tvdbShowId && ov.tvdbSeason != null
                ? `${ov.tvdbShowId}:${ov.tvdbSeason}` : (r.tvdb_season ?? '');

              const { seasons: spTvdbSeasons, opts: _tvdbSOpts } =
                buildTvdbSeasonOptions(currentEntryId, r.show_name, searchResults, episodeData, ov?.tvdbShowId);
              const { opts: tvdbEpOpts, title: tvdbEpTitle } =
                buildTvdbEpOptions(r.tvdb_season, spTvdbSeasons);

              return (
                <MappingCard
                  key={i} row={r} rowIndex={i} variant="sp"
                  torrentName={torrentName}
                  onSubtitleUploaded={handleSubtitleUploaded}
                  {...subProps(r.file_name)}
                  bgmEntryOptions={bgmEntryOptions}
                  currentEps={currentEps} currentEntryId={currentEntryId}
                  tmdbSeasonOptions={tmdbSeasonOpts} tmdbSeasonValue={tmdbSeasonVal}
                  tmdbEpOptions={tmdbEpOpts} tmdbEpValue={r.tmdb_ep ?? ''} tmdbEpTitle={r.tmdb_ep_name}
                  tvdbSeasonOptions={tvdbSeasonOpts} tvdbSeasonValue={tvdbSeasonVal}
                  tvdbEpOptions={tvdbEpOpts} tvdbEpValue={r.tvdb_ep ?? ''} tvdbEpTitle={tvdbEpTitle}
                  onBgmEntryChange={(v) => handleBgmEntryChange(i, v)}
                  onBgmEpChange={(v) => handleBgmEpChange(i, currentEntryId, v)}
                  onTmdbSeasonChange={(v) => handleTmdbSeasonChange(i, r.show_name, v)}
                  onTmdbEpChange={(v) => handleTmdbEpChange(i, v)}
                  onTvdbSeasonChange={(v) => handleTvdbSeasonChange(i, v)}
                  onTvdbEpChange={(v) => handleTvdbEpChange(i, v)}
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
