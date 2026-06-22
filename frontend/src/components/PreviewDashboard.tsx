import { useMemo } from 'react';
import type { TorrentPreviewResponse } from '../types/preview';
import MappingOverviewCard from './Cards/MappingOverviewCard';

interface Props {
  data: TorrentPreviewResponse;
  onConfirm: () => void;
  onCancel: () => void;
}

export default function PreviewDashboard({ data, onConfirm, onCancel }: Props) {
  const stats = useMemo(() => {
    const entries = Object.values(data.episodes);
    const matched = entries.filter(ep => ep.tmdb && ep.bangumi_ep_id).length;
    const needsWork = entries.filter(ep => !ep.tmdb || !ep.bangumi_ep_id).length;
    return { total: entries.length, ready: matched, needsWork };
  }, [data.episodes]);

  return (
    <>
      <MappingOverviewCard data={data} />

      {/* Fixed Footer — 1:1 template replication */}
      <footer className="fixed bottom-0 left-64 right-0 bg-background/90 backdrop-blur-xl border-t border-border/30 p-6 z-40">
        <div className="max-w-[1200px] mx-auto flex flex-col sm:flex-row justify-between items-center gap-6">
          {/* Left: Queue summary */}
          <div className="flex items-center gap-6">
            <div className="flex -space-x-3">
              {/* Ready count */}
              {stats.ready > 0 && (
                <div className="w-8 h-8 rounded-full bg-secondary/60 border-2 border-background flex items-center justify-center text-[10px] font-bold text-white">
                  {stats.ready}
                </div>
              )}
              {/* Needs work count */}
              {stats.needsWork > 0 && (
                <div className="w-8 h-8 rounded-full bg-accent border-2 border-background flex items-center justify-center text-[10px] font-bold text-white">
                  {stats.needsWork}
                </div>
              )}
              {/* Extra indicator */}
              {stats.total > (stats.ready + stats.needsWork) && (
                <div className="w-8 h-8 rounded-full bg-muted border-2 border-background flex items-center justify-center text-[10px] font-bold text-muted-foreground">
                  +{stats.total - stats.ready - stats.needsWork}
                </div>
              )}
            </div>
            <div>
              <p className="text-base font-semibold text-foreground leading-none">
                {stats.total} Torrent{stats.total !== 1 ? 's' : ''} Staged
              </p>
              <p className="text-sm text-muted-foreground">
                Ready to process {stats.ready} file{stats.ready !== 1 ? 's' : ''}
                {stats.needsWork > 0 && `, ${stats.needsWork} require${stats.needsWork === 1 ? 's' : ''} manual intervention`}.
              </p>
            </div>
          </div>

          {/* Right: Actions */}
          <div className="flex items-center gap-3">
            <button
              onClick={onCancel}
              className="px-12 py-3 border border-border text-muted-foreground rounded-full text-xs font-semibold tracking-wide hover:bg-muted transition-colors cursor-pointer"
            >
              Clear Queue
            </button>
            <button
              onClick={onConfirm}
              className="px-20 py-3 bg-primary text-primary-foreground rounded-full text-xs font-semibold tracking-wide shadow-lg shadow-primary/20 flex items-center gap-2 hover:scale-105 active:scale-95 transition-all cursor-pointer"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="9 11 12 14 22 4" />
                <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
              </svg>
              Begin Processing
            </button>
          </div>
        </div>
      </footer>
    </>
  );
}
