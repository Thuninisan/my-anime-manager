import { useMemo } from 'react';
import MatchTable from '@/components/MatchTable';
import InfoCards from '@/components/InfoCards';

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

  // Count matched via MatchTable's computeMatches (imported separately)
  // For now estimate: count show_names that have both TMDB and Bangumi in search_results
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

export default function TorrentPreview({
  searchResult,
  augmentedEpData,
  onEpisodeDataChange,
  onClose,
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
      <MatchTable data={mergedResult} />

      {/* ── Floating action button ── */}
      <div className="fixed bottom-0 left-64 right-0 p-8 pointer-events-none z-50">
        <div className="flex justify-center">
          <button
            className="pointer-events-auto flex items-center gap-3 px-8 py-4 bg-primary text-white rounded-2xl shadow-2xl shadow-pink-500/40 hover:scale-[1.02] active:scale-95 transition-all group cursor-pointer"
            onClick={() => {
              // TODO: wire up to confirm/preview flow
              alert('Processing not yet wired for this preview mode');
            }}
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" className="animate-pulse">
              <path d="M8 5v14l11-7z" />
            </svg>
            <span className="font-bold text-lg tracking-tight">Begin Processing All Matches</span>
            <span className="text-xs bg-white/20 px-2 py-1 rounded-md font-mono">
              {parsedFiles.length} Files
            </span>
          </button>
        </div>
      </div>
    </div>
  );
}
