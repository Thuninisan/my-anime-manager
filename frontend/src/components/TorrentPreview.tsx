import { useMemo, useState, useCallback } from 'react';
import MatchTable, { type MatchRow } from '@/components/MatchTable';
import InfoCards from '@/components/InfoCards';
import { submitDownload, type DownloadFileEntry, type UploadedSubEntry } from '@/api/torrentApi';

interface TorrentPreviewProps {
  searchResult: any;
  augmentedEpData: any;
  onEpisodeDataChange: (data: any) => void;
  onClose: () => void;
}

/** Compute stats from the parsed + matched data. */
function computeStats(searchResult: any) {
  const parsedFiles = searchResult?.parsed_files || [];
  const skippedFiles = searchResult?.skipped_files || [];
  const total = parsedFiles.length;

  const searchResults = searchResult?.search_results || {};
  let mapped = 0;
  for (const pf of parsedFiles) {
    const entry = searchResults[pf.show_name];
    if (entry?.tmdb && entry?.bangumi) mapped++;
  }

  return {
    total: total + skippedFiles.length,
    mapped,
    pending: total - mapped,
  };
}

/** Extract tmdb show name from search results for a given show_name. */
function getTmdbShowName(searchResult: any, showName: string): string {
  const entry = searchResult?.search_results?.[showName];
  return entry?.tmdb?.name || showName;
}

/** Extract bangumi show name from search results or from the row. */
function getBangumiShowName(searchResult: any, row: MatchRow): string {
  // Use the BGM entry name from search results if available
  for (const entry of Object.values(searchResult?.search_results || {}) as any[]) {
    if (entry?.bangumi?.id === row.bgm_entry_id) {
      return entry.bangumi.name_cn || entry.bangumi.name || row.bgm_entry;
    }
  }
  return row.bgm_entry || 'Unknown';
}

export default function TorrentPreview({
  searchResult,
  augmentedEpData,
  onEpisodeDataChange,
  onClose: _onClose,
}: TorrentPreviewProps) {
  const mergedResult = augmentedEpData && searchResult
    ? { ...searchResult, episode_data: augmentedEpData }
    : searchResult;

  const stats = useMemo(() => computeStats(searchResult), [searchResult]);

  const parsedFiles = searchResult?.parsed_files || [];
  const skippedFiles = searchResult?.skipped_files || [];
  const movieCount = useMemo(
    () => parsedFiles.filter((pf: any) => {
      const entry = searchResult?.search_results?.[pf.show_name];
      return entry?.media_type === 'movie';
    }).length,
    [parsedFiles, searchResult],
  );
  const tvCount = parsedFiles.length - movieCount;

  // ── State lifted from MatchTable (for download button) ──
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

      for (const row of matchedRows) {
        const tmdbName = getTmdbShowName(searchResult, row.show_name);
        const bgmName = getBangumiShowName(searchResult, row);

        // Common NFO metadata for this row
        const nfoMeta = {
          bangumi_id: row.bgm_entry_id ?? 0,
          bangumi_ep_id: row.bgm_ep_id,
          tmdb_season: row.tmdb_season ?? 0,
          tmdb_episode: row.tmdb_ep ?? 0,
        };

        // Video file
        files.push({
          torrent_path: row.torrent_path,
          is_subtitle: false,
          tmdb_show_name: tmdbName,
          bangumi_show_name: bgmName,
          bangumi_sort: row.bgm_sort ?? row.src_episode,
          ...nfoMeta,
        });

        // Matching subtitle files from the torrent
        const videoStem = row.file_name.replace(/\.[^.]+$/, '').toLowerCase();
        const torrentSubtitles: string[] = searchResult?.subtitles || [];
        for (const sub of torrentSubtitles) {
          if (sub.replace(/\.[^.]+$/, '').toLowerCase() === videoStem) {
            // Find the torrent_path for this subtitle from parsed_files or skipped_files
            const allFiles = [
              ...(searchResult?.parsed_files || []),
              ...(searchResult?.skipped_files || []),
            ];
            const subInfo = allFiles.find((f: any) => f.file_name === sub);
            files.push({
              torrent_path: subInfo?.torrent_path || sub,
              is_subtitle: true,
              tmdb_show_name: tmdbName,
              bangumi_show_name: bgmName,
              bangumi_sort: row.bgm_sort ?? row.src_episode,
              ...nfoMeta,
            });
          }
        }
      }

      // Compile uploaded subtitles
      const uploadedSubs: UploadedSubEntry[] = [];
      for (const usub of uploadedSubtitles) {
        // Find which matched row this subtitle belongs to (by stem match)
        const subStem = usub.storedFilename.replace(/\.[^.]+$/, '').toLowerCase();
        const matchingRow = matchedRows.find(
          (r) => r.file_name.replace(/\.[^.]+$/, '').toLowerCase() === subStem,
        );
        if (matchingRow) {
          uploadedSubs.push({
            stored_filename: usub.storedFilename,
            original_filename: usub.originalFilename,
            tmdb_show_name: getTmdbShowName(searchResult, matchingRow.show_name),
            bangumi_show_name: getBangumiShowName(searchResult, matchingRow),
            bangumi_sort: matchingRow.bgm_sort ?? matchingRow.src_episode,
            bangumi_id: matchingRow.bgm_entry_id ?? 0,
            bangumi_ep_id: matchingRow.bgm_ep_id,
            tmdb_season: matchingRow.tmdb_season ?? 0,
            tmdb_episode: matchingRow.tmdb_ep ?? 0,
          });
        }
      }

      const result = await submitDownload({
        torrent_path: searchResult.torrent_path,
        torrent_name: searchResult.torrent_name,
        files,
        uploaded_subtitles: uploadedSubs,
      });

      setDownloadResult(result.message);
    } catch (err: any) {
      setDownloadError(err.message || 'Download failed');
    } finally {
      setDownloading(false);
    }
  }, [effectiveRows, uploadedSubtitles, searchResult]);

  return (
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
      <InfoCards
        searchResult={searchResult}
        onEpisodeDataChange={onEpisodeDataChange}
      />

      {/* ── Match tables ── */}
      <MatchTable
        data={mergedResult}
        onRowsComputed={handleRowsComputed}
        onSubtitlesChange={handleSubtitlesChange}
      />

      {/* ── Success / error message ── */}
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

      {/* ── Floating action button ── */}
      <div className="fixed bottom-0 left-64 right-0 p-8 pointer-events-none z-50">
        <div className="flex justify-center">
          <button
            className="pointer-events-auto flex items-center gap-3 px-8 py-4 bg-primary text-white rounded-2xl shadow-2xl shadow-pink-500/40 hover:scale-[1.02] active:scale-95 transition-all group cursor-pointer disabled:opacity-60 disabled:cursor-not-allowed"
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
        </div>
      </div>
    </div>
  );
}
