import type { TorrentPreviewResponse } from '@/types/preview';
import type { EpisodeCatalog } from '@/types/episode';
import { useMemo, useState, useCallback } from 'react';
import MatchTable, { type MatchRow } from '@/components/torrent/MatchTable';
import { subtitleStatus, subtitleDestinationSuffix, type SubtitleAssociations, type SubtitleFilter } from '@/lib/subtitleMatching';
import InfoCards from '@/components/torrent/InfoCards';
import { submitDownload, type DownloadFileEntry, type UploadedSubEntry } from '@/api/torrentApi';

interface TorrentPreviewProps {
  replaceBangumiId?: number;
  searchResult: TorrentPreviewResponse;
  augmentedEpData: EpisodeCatalog | null;
  onEpisodeDataChange: (data: EpisodeCatalog) => void;
  onClose: () => void;
  onTorrentAdded?: () => void;
}

/** Compute stats from the parsed + matched data. */
function computeStats(searchResult: TorrentPreviewResponse) {
  const parsedFiles = searchResult?.parsed_files || [];
  const skippedFiles = searchResult?.skipped_files || [];
  const total = parsedFiles.length;

  const searchResults = searchResult?.search_results || {};
  let mapped = 0;
  for (const pf of parsedFiles) {
    const entry = searchResults[pf.show_name];
    if (entry?.tmdb_series_id && entry?.bangumi_subject_id) mapped++;
  }

  return {
    total: total + skippedFiles.length,
    mapped,
    pending: total - mapped,
  };
}

export default function TorrentPreview({
  replaceBangumiId,
  searchResult: initialSearchResult,
  augmentedEpData,
  onEpisodeDataChange,
  onClose,
  onTorrentAdded,
}: TorrentPreviewProps) {
  const [sessionView, setSessionView] = useState(initialSearchResult);
  const searchResult = sessionView.preview_id === initialSearchResult.preview_id ? sessionView : initialSearchResult;
  const mergedResult = augmentedEpData && searchResult
    ? { ...searchResult, episode_catalog: augmentedEpData }
    : searchResult;

  const stats = useMemo(() => computeStats(searchResult), [searchResult]);

  const parsedFiles = searchResult?.parsed_files || [];
  const skippedFiles = searchResult?.skipped_files || [];
  const movieCount = useMemo(
    () => parsedFiles.filter((pf) => {
      const entry = searchResult?.search_results?.[pf.show_name];
      return entry?.media_type === 'movie';
    }).length,
    [parsedFiles, searchResult],
  );
  const tvCount = parsedFiles.length - movieCount;

  // ── State lifted from MatchTable (for download button) ──
  const [associations, setAssociations] = useState<SubtitleAssociations>({});
  const [subtitleFilter, setSubtitleFilter] = useState<SubtitleFilter>('all');
  const [effectiveRows, setEffectiveRows] = useState<MatchRow[]>([]);
  const [uploadedSubtitles, setUploadedSubtitles] = useState<
    { originalFilename: string; storedFilename: string }[]
  >([]);

  const handleRowsComputed = useCallback((rows: MatchRow[]) => {
    setEffectiveRows(rows);
  }, []);

  const handleSubtitlesChange = useCallback(
    (subs: { originalFilename: string; storedFilename: string }[]) => {
      setUploadedSubtitles(subs);
    },
    [],
  );

  // ── Download submission ──
  const [downloading, setDownloading] = useState(false);
  const [downloadResult, setDownloadResult] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  const handleBeginProcessing = useCallback(async () => {
    if (effectiveRows.length === 0) return;

    setDownloading(true);
    setDownloadError(null);
    setDownloadResult(null);

    try {
      // Compile the files list from matched rows
      const files: DownloadFileEntry[] = [];
      const matchedRows = effectiveRows.filter((r) => r.matched);

      const subtitleSuffix = (row: MatchRow, id: string) => {
        const selected = associations[row.torrent_path]?.selected || [];
        return subtitleDestinationSuffix(selected.find(s => s.id === id)!, selected);
      };
      const requiredFileId = (path: string): string => {
        const file = [...searchResult.parsed_files, ...(searchResult.specials || []), ...(searchResult.subtitle_files || [])]
          .find(file => file.torrent_path === path);
        if (!file?.file_id) throw new Error('Preview file identity missing; please preview again.');
        return file.file_id;
      };
      for (const row of matchedRows) {
        files.push({ file_id: requiredFileId(row.torrent_path), mapping: row.mapping });
        for (const sub of associations[row.torrent_path]?.selected || []) {
          if (sub.source !== 'torrent') continue;
          files.push({ file_id: requiredFileId(sub.path), mapping: row.mapping, subtitle_suffix: subtitleSuffix(row, sub.id) });
        }
      }

      // Compile uploaded subtitles
      const uploadedSubs: UploadedSubEntry[] = [];
      for (const usub of uploadedSubtitles) {
        // Find which matched row this subtitle belongs to (by stem match)
        const matchingRow = matchedRows.find(r => associations[r.torrent_path]?.selected?.some(s => s.id === `upload:${usub.storedFilename}`));
        if (matchingRow) {
          uploadedSubs.push({
            subtitle_suffix: subtitleSuffix(matchingRow, `upload:${usub.storedFilename}`),
            file_id: requiredFileId(matchingRow.torrent_path),
            stored_filename: usub.storedFilename,
            original_filename: usub.originalFilename,
            mapping: matchingRow.mapping,
          });
        }
      }

      const result = await submitDownload({
        preview_id: searchResult.preview_id,
        preview_revision: searchResult.revision,
        replace_bangumi_id: replaceBangumiId,
        resource_id: searchResult.resource_id,
        files,
        uploaded_subtitles: uploadedSubs,

      });

      setDownloadResult(result.message);
      onTorrentAdded?.();
    } catch (err) {
      setDownloadError(err instanceof Error ? err.message : 'Download failed');
    } finally {
      setDownloading(false);
    }
  }, [effectiveRows, associations, uploadedSubtitles, searchResult, replaceBangumiId, onTorrentAdded]);

  const torrentName: string = searchResult?.torrent_name || 'Torrent Preview';
  const success = downloadResult && !downloading;

  return (
    <div className="flex flex-col min-h-full">
      {/* ── Header bar ── */}
      <div className="sticky top-0 z-30 bg-background/95 backdrop-blur-md border-b border-border-light dark:border-border-dark px-8 py-4 flex items-center justify-between">
        <div className="flex items-center gap-3 min-w-0">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-primary shrink-0">
            <rect x="2" y="7" width="20" height="15" rx="2" ry="2" />
            <polyline points="17 2 12 7 7 2" />
          </svg>
          <h2 className="text-base font-semibold truncate">{torrentName}</h2>
        </div>
        <button
          className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm text-muted-foreground hover:text-foreground hover:bg-muted rounded-lg transition-colors cursor-pointer shrink-0"
          onClick={onClose}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
          <span>Close</span>
        </button>
      </div>

      {/* ── Body ── */}
      <div className="flex-1 overflow-y-auto custom-scrollbar p-8 pb-32">
        {/* ── Stats cards ── */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-8">
          <div className="bg-surface-light dark:bg-surface-dark border border-border-light dark:border-border-dark p-4 rounded-xl shadow-sm">
            <p className="text-xs text-slate-400 font-bold uppercase mb-1">Total Files</p>
            <div className="flex items-baseline gap-2">
              <span className="text-2xl font-bold">{stats.total}</span>
              <span className="text-xs text-slate-500">
                {skippedFiles.length} skipped
              </span>
            </div>
          </div>
          <div className="bg-surface-light dark:bg-surface-dark border border-border-light dark:border-border-dark p-4 rounded-xl shadow-sm">
            <p className="text-xs text-slate-400 font-bold uppercase mb-1">Mapped</p>
            <div className="flex items-baseline gap-2 text-primary">
              <span className="text-2xl font-bold">{stats.mapped}</span>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
                <polyline points="22 4 12 14.01 9 11.01" />
              </svg>
            </div>
          </div>
          <div className="bg-surface-light dark:bg-surface-dark border border-border-light dark:border-border-dark p-4 rounded-xl shadow-sm">
            <p className="text-xs text-slate-400 font-bold uppercase mb-1">Pending</p>
            <div className="flex items-baseline gap-2 text-secondary">
              <span className="text-2xl font-bold">{stats.pending}</span>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="10" />
                <polyline points="12 6 12 12 16 14" />
              </svg>
            </div>
          </div>
          <div className="bg-surface-light dark:bg-surface-dark border border-border-light dark:border-border-dark p-4 rounded-xl shadow-sm">
            <p className="text-xs text-slate-400 font-bold uppercase mb-1">Breakdown</p>
            <div className="flex items-baseline gap-3">
              <span className="text-sm font-medium">
                <span className="text-primary">{tvCount}</span> TV
              </span>
              <span className="text-sm font-medium">
                <span className="text-secondary">{movieCount}</span> Movie
              </span>
            </div>
          </div>
        </div>

        {/* ── Metadata Source Overrides ── */}
        {(searchResult.resource_candidates?.length ?? 0) > 0 && (
          <div className="rounded-xl border border-primary/30 bg-primary/5 p-4 text-sm">
            <p className="font-semibold">资源预识别的 Bangumi 候选</p>
            <p className="mt-1 text-muted-foreground">请在下方逐文件确认剧集对应关系。</p>
            <ul className="mt-2 flex flex-wrap gap-2">
              {searchResult.resource_candidates?.map((candidate) => (
                <li key={`${candidate.provider}-${candidate.provider_id}-${candidate.media_type}`} className="rounded-md border border-border px-2 py-1">
                  {candidate.provider.toUpperCase()} {candidate.provider_id} · {candidate.media_type === 'movie' ? '电影' : '剧集'} · {candidate.title || candidate.source}
                </li>
              ))}
            </ul>
          </div>
        )}
        <InfoCards
          searchResult={searchResult}
          episodeDataOverride={mergedResult.episode_catalog}
          onPreviewChange={view => { setSessionView(view); onEpisodeDataChange(view.episode_catalog); }}
          onEpisodeDataChange={onEpisodeDataChange}
        />

        {/* ── Match tables ── */}
        <MatchTable
          key={searchResult.preview_id}
          data={mergedResult}
          subtitleFilter={subtitleFilter}
          onAssociationsChange={setAssociations}
          onRowsComputed={handleRowsComputed}
          onSubtitlesChange={handleSubtitlesChange}
        />

        {/* ── Success / error toasts ── */}
        {downloadResult && (
          <div className="fixed top-4 right-4 z-50 bg-accent text-white px-5 py-3 rounded-xl shadow-lg max-w-md">
            <p className="text-sm font-semibold">{downloadResult}</p>
            <button
              className="text-xs underline mt-1 cursor-pointer"
              onClick={() => setDownloadResult(null)}
            >
              Dismiss
            </button>
          </div>
        )}
        {downloadError && (
          <div className="fixed top-4 right-4 z-50 bg-destructive text-white px-5 py-3 rounded-xl shadow-lg max-w-md">
            <p className="text-sm font-semibold">Error: {downloadError}</p>
            <button
              className="text-xs underline mt-1 cursor-pointer"
              onClick={() => setDownloadError(null)}
            >
              Dismiss
            </button>
          </div>
        )}
      </div>

      {/* ── Bottom action bar ── */}
      <div className="sticky bottom-0 bg-background/95 backdrop-blur-md border-t border-border-light dark:border-border-dark px-8 py-5 z-30">
        {!success && <div className="mb-3 flex flex-wrap items-center justify-center gap-3 text-xs">
          <span>已选 {effectiveRows.filter(r => r.matched).length} 个视频</span>
          {(['linked', 'missing', 'pending'] as const).map(status => <span key={status}>{effectiveRows.filter(r => r.matched && subtitleStatus(associations[r.torrent_path]) === status).length} 个{status === 'linked' ? '已选字幕' : status === 'missing' ? '未选字幕' : '待确认'}</span>)}
          {(['all', 'missing', 'pending'] as const).map(filter => <button key={filter} type="button" aria-pressed={subtitleFilter === filter}
            className={`rounded-md border px-2 py-1 cursor-pointer ${subtitleFilter === filter ? 'border-primary text-primary bg-primary/10' : 'border-border text-muted-foreground'}`}
            onClick={() => { setSubtitleFilter(filter); document.getElementById('torrent-file-matches')?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }}>
            {filter === 'all' ? '全部视频' : filter === 'missing' ? '查看未选字幕' : '查看待确认'}</button>)}
          <span className="text-muted-foreground">仅统计外置字幕</span>
        </div>}
        <div className="flex justify-center">
          {success ? (
            /* ── Success state: offer to process another torrent ── */
            <div className="flex items-center gap-4">
              <div className="flex items-center gap-2 text-accent">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
                  <polyline points="22 4 12 14.01 9 11.01" />
                </svg>
                <span className="font-semibold">Download Complete</span>
              </div>
              <button
                className="flex items-center gap-2 px-6 py-3 bg-primary text-white rounded-2xl shadow-2xl shadow-pink-500/40 hover:scale-[1.02] active:scale-95 transition-all cursor-pointer font-bold text-lg tracking-tight"
                onClick={onClose}
              >
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="12" y1="5" x2="12" y2="19" />
                  <line x1="5" y1="12" x2="19" y2="12" />
                </svg>
                Process Another Torrent
              </button>
            </div>
          ) : (
            /* ── Default: Begin Processing button ── */
            <button
              className="flex items-center gap-3 px-8 py-4 bg-primary text-white rounded-2xl shadow-2xl shadow-pink-500/40 hover:scale-[1.02] active:scale-95 transition-all group cursor-pointer disabled:opacity-60 disabled:cursor-not-allowed"
              onClick={handleBeginProcessing}
              disabled={downloading || effectiveRows.filter((r) => r.matched).length === 0}
            >
              {downloading ? (
                <>
                  <div className="w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  <span className="font-bold text-lg tracking-tight">Submitting...</span>
                </>
              ) : (
                <>
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" className="animate-pulse">
                    <path d="M8 5v14l11-7z" />
                  </svg>
                  <span className="font-bold text-lg tracking-tight">Begin Processing All Matches</span>
                  <span className="text-xs bg-white/20 px-2 py-1 rounded-md font-mono">
                    {effectiveRows.filter((r) => r.matched).length} / {parsedFiles.length} Files
                  </span>
                </>
              )}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
