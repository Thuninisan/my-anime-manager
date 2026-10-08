import type { TorrentPreviewResponse } from '@/types/preview';
/** Matching logic: parsed_files → search_results → episode_catalog → table.

   1. parsed_file.show_name → search_results[key]
   2. parsed_file.parsed → provider candidates from EpisodeCatalog
   3. Selected matching strategy preserves provider numbering and identity
   4. All file coordinates live in MatchRow.mapping
   5. Manual dropdown overrides update the same canonical mapping

   BGM Entry / BGM Name columns have dropdowns populated from
   search_results + episode_catalog so the user can override the
   auto-matched entry and episode.

   State management and matching logic live in:
     hooks/useMatchOverrides.ts   — overrides, handlers, rows computation
     hooks/useSubtitleMatching.ts — subtitle upload/delete/batch
     lib/matchUtils.ts            — pure matching functions + options builders
     types/matchTable.ts          — shared type definitions
*/

import { useEffect } from 'react';
import SubtitlePanel from '@/components/torrent/SubtitlePanel';
import { subtitleStatus, type SubtitleAssociations, type SubtitleFilter } from '@/lib/subtitleMatching';
import MappingCard from '@/components/torrent/MappingCard';
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

export default function MatchTable({ data, onRowsComputed, onSubtitlesChange, onAssociationsChange, subtitleFilter = 'all' }: {
  data: TorrentPreviewResponse;
  subtitleFilter?: SubtitleFilter;
  onAssociationsChange?: (value: SubtitleAssociations) => void;
  onRowsComputed?: (rows: MatchRow[]) => void;
  onSubtitlesChange?: (subs: { originalFilename: string; storedFilename: string }[]) => void;
}) {
  const searchResults = data.search_results || {};
  const episodeData = data.episode_catalog || { tmdb: {}, bangumi: {} };
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
    uploadedSubtitles, associations, subtitleFiles, setAssociation, preference, changePreference, setSelected,
    handleSubtitleUploaded,
    makeHandleSubtitleDeleted,
    batchFolderRef,
    batchProcessing,
    batchProgress,
    handleBatchFolderUpload,
  } = useSubtitleMatching(subtitles, torrentName, rows);

  useEffect(() => { onAssociationsChange?.(associations); }, [associations, onAssociationsChange]);
  const visible = (r: MatchRow) => subtitleFilter === 'all' || (r.matched && subtitleStatus(associations[r.torrent_path]) === subtitleFilter);
  // ── Notify parent ──
  useEffect(() => { onRowsComputed?.(rows); }, [rows, onRowsComputed]);
  useEffect(() => { onSubtitlesChange?.(uploadedSubtitles); }, [uploadedSubtitles, onSubtitlesChange]);

  // ── Show match errors via toast ──
  useEffect(() => {
    if (matchError) showError(matchError);
  }, [matchError]);

  // ── Shared subtitle callbacks ──
  const subProps = (row: MatchRow) => {
    return {
      subtitlePanel: <SubtitlePanel state={associations[row.torrent_path] || { linked: [], candidates: [] }} files={subtitleFiles}
        onAssociate={(id, linked) => setSelected(id, row.torrent_path, linked)}
        onUnlink={id => setAssociation(id, null)}
        onDelete={file => makeHandleSubtitleDeleted(file.path)()} />,
    };
  };

  return (
    <div className="space-y-10 scroll-mt-20" id="torrent-file-matches">
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <span className="font-semibold">字幕选择</span>
        {(['all', 'simplified', 'traditional'] as const).map(value => <button key={value} type="button" aria-pressed={preference === value}
          className={`rounded-md border px-3 py-1.5 cursor-pointer ${preference === value ? 'border-primary text-primary bg-primary/10' : 'border-border text-muted-foreground'}`}
          onClick={() => changePreference(value)}>{value === 'all' ? '全部' : value === 'simplified' ? '简体' : '繁体'}</button>)}
        <span className="text-xs text-muted-foreground">按文件名识别，语言未知默认保留；切换选项会重置逐文件选择。</span>
      </div>
      {subtitleFilter !== 'all' && !rows.some(visible) && <p className="text-sm text-muted-foreground">当前筛选下没有已选视频。</p>}
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
            {movieRows.filter(visible).map((r) => {
              const i = r._idx;
              const currentEntryId = r.mapping.bangumi.subject_id ?? 0;
              const currentEps = r.mapping.bangumi.subject_id ? getBgmEpisodes(r.mapping.bangumi.subject_id) : [];
              return (
                <MappingCard
                  key={i} row={r} rowIndex={i} variant="movie"
                  torrentName={torrentName}
                  onSubtitleUploaded={(original, stored) => { handleSubtitleUploaded(original, stored); setAssociation(`upload:${stored}`, r.torrent_path); }}
                  {...subProps(r)}
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
            {tvRows.filter(visible).map((r) => {
              const i = r._idx;
              const currentEps = r.mapping.bangumi.subject_id ? getBgmEpisodes(r.mapping.bangumi.subject_id) : [];
              const currentEntryId = r.mapping.bangumi.subject_id ?? 0;

              // TMDB options
              const { seasons: tmdbSeasons, opts: tmdbSeasonOpts } =
                buildTmdbSeasonOptions(r.show_name, searchResults, episodeData);
              const tmdbEpOpts = buildTmdbEpOptions(r.mapping.tmdb.season_number, tmdbSeasons);

              // TVDB options
              const { seasons: tvdbSeasons, opts: tvdbSeasonOpts } =
                buildTvdbSeasonOptions(currentEntryId, r.show_name, searchResults, episodeData, overrides[i]?.tvdbShowId);
              const { opts: tvdbEpOpts, title: tvdbEpTitle } =
                buildTvdbEpOptions(r.mapping.tvdb.season_number, tvdbSeasons);

              return (
                <MappingCard
                  key={i} row={r} rowIndex={i} variant="tv"
                  torrentName={torrentName}
                  onSubtitleUploaded={(original, stored) => { handleSubtitleUploaded(original, stored); setAssociation(`upload:${stored}`, r.torrent_path); }}
                  {...subProps(r)}
                  bgmEntryOptions={bgmEntryOptions}
                  currentEps={currentEps} currentEntryId={currentEntryId}
                  tmdbSeasonOptions={tmdbSeasonOpts} tmdbSeasonValue={r.mapping.tmdb.season_number ?? ''}
                  tmdbEpOptions={tmdbEpOpts} tmdbEpValue={r.mapping.tmdb.episode_number ?? ''} tmdbEpTitle={r.tmdb_ep_name}
                  tvdbSeasonOptions={tvdbSeasonOpts} tvdbSeasonValue={r.mapping.tvdb.season_number ?? ''}
                  tvdbEpOptions={tvdbEpOpts} tvdbEpValue={r.mapping.tvdb.episode_number ?? ''} tvdbEpTitle={tvdbEpTitle}
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
            {spRows.filter(visible).map((r) => {
              const i = r._idx;
              const currentEps = r.mapping.bangumi.subject_id ? getBgmEpisodes(r.mapping.bangumi.subject_id) : [];
              const currentEntryId = r.mapping.bangumi.subject_id ?? 0;
              const ov = overrides[i];

              // SP TMDB options (aggregate all shows)
              const tmdbSeasonOpts = buildSpSeasonOptions(episodeData, searchResults, 'tmdb');
              const tmdbSeasonVal = ov?.tmdbShowId && ov.tmdbSeason != null
                ? `${ov.tmdbShowId}:${ov.tmdbSeason}` : (r.mapping.tmdb.season_number ?? '');

              const lookupTmdbId = ov?.tmdbShowId ?? searchResults[r.show_name]?.tmdb_series_id;
              const lookupSeasons: Record<string, TmdbSeason> =
                (lookupTmdbId && episodeData.tmdb?.[String(lookupTmdbId)]) || {};
              const spTmdbSeasons: Record<string, TmdbSeason> =
                Object.keys(lookupSeasons).length > 0 ? lookupSeasons : mergeAllTmdbSeasons(episodeData);
              const tmdbEpOpts = buildTmdbEpOptions(r.mapping.tmdb.season_number, spTmdbSeasons);

              // SP TVDB options (aggregate all shows)
              const tvdbSeasonOpts = buildSpSeasonOptions(episodeData, searchResults, 'tvdb');
              const tvdbSeasonVal = ov?.tvdbShowId && ov.tvdbSeason != null
                ? `${ov.tvdbShowId}:${ov.tvdbSeason}` : (r.mapping.tvdb.season_number ?? '');

              const { seasons: spTvdbSeasons, opts: _tvdbSOpts } =
                buildTvdbSeasonOptions(currentEntryId, r.show_name, searchResults, episodeData, ov?.tvdbShowId);
              const { opts: tvdbEpOpts, title: tvdbEpTitle } =
                buildTvdbEpOptions(r.mapping.tvdb.season_number, spTvdbSeasons);

              return (
                <MappingCard
                  key={i} row={r} rowIndex={i} variant="sp"
                  torrentName={torrentName}
                  onSubtitleUploaded={(original, stored) => { handleSubtitleUploaded(original, stored); setAssociation(`upload:${stored}`, r.torrent_path); }}
                  {...subProps(r)}
                  bgmEntryOptions={bgmEntryOptions}
                  currentEps={currentEps} currentEntryId={currentEntryId}
                  tmdbSeasonOptions={tmdbSeasonOpts} tmdbSeasonValue={tmdbSeasonVal}
                  tmdbEpOptions={tmdbEpOpts} tmdbEpValue={r.mapping.tmdb.episode_number ?? ''} tmdbEpTitle={r.tmdb_ep_name}
                  tvdbSeasonOptions={tvdbSeasonOpts} tvdbSeasonValue={tvdbSeasonVal}
                  tvdbEpOptions={tvdbEpOpts} tvdbEpValue={r.mapping.tvdb.episode_number ?? ''} tvdbEpTitle={tvdbEpTitle}
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
