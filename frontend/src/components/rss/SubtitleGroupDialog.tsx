import type { BangumiMeta, BangumiRssResponse, RssFeedResponse, SubscriptionOut } from '@/types/preview';
import SubtitleGroupTable from './SubtitleGroupTable';

interface Props {
  result: BangumiRssResponse;
  meta: BangumiMeta | null;
  subscriptions: SubscriptionOut[];
  expanded: Record<string, RssFeedResponse | null>;
  loadingFeed: Record<string, boolean>;
  filterTags: Record<number, string[]>;
  tagBoxOpen: Record<number, boolean>;
  subscribingId: number | null;
  excludePatterns: Record<number, string[]>;
  onToggleFeed: (url: string) => void;
  onToggleTag: (subgroupId: number, tag: string) => void;
  onToggleTagBox: (subgroupId: number) => void;
  onSubscribe: (group: { name: string; subgroup_id: number; rss_url: string }, role: 'primary' | 'backup') => void;
  onExcludeChange: (subgroupId: number, patterns: string[]) => void;
  getSubMode: (subgroupId: number) => 'primary' | 'backup' | null;
  onClose: () => void;
}

export default function SubtitleGroupDialog({
  result, meta, subscriptions, expanded, loadingFeed, filterTags, tagBoxOpen,
  subscribingId, excludePatterns,
  onToggleFeed, onToggleTag, onToggleTagBox, onSubscribe,
  onExcludeChange,
  getSubMode, onClose,
}: Props) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-md p-4">
      <div className="bg-card w-full max-w-5xl h-[85vh] rounded-xl shadow-2xl overflow-hidden flex flex-col border border-border">
        {/* Header */}
        <header className="relative bg-muted/20 border-b border-border px-6 py-5 shrink-0">
          {/* Close button */}
          <button
            className="absolute top-4 right-4 p-1.5 text-muted-foreground hover:text-rose-500 transition-colors cursor-pointer"
            onClick={onClose}
            aria-label="Close"
          >
            <svg className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
              <path d="M6 18L18 6M6 6l12 12" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>

          <div className="flex flex-col sm:flex-row gap-5 items-start">
            {/* Cover image */}
            {meta?.poster_url ? (
              <div className="w-24 h-32 sm:w-28 sm:h-40 flex-shrink-0 rounded-lg overflow-hidden border border-border shadow-sm bg-muted">
                <img src={meta.poster_url} alt={result.name} className="w-full h-full object-cover" />
              </div>
            ) : (
              <div className="w-24 h-32 sm:w-28 sm:h-40 flex-shrink-0 rounded-lg overflow-hidden border border-border shadow-sm bg-muted flex items-center justify-center">
                <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="text-muted-foreground/40">
                  <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M23 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" />
                </svg>
              </div>
            )}

            {/* Content */}
            <div className="flex-1 flex flex-col gap-2.5 min-w-0">
              <div className="flex items-center gap-3 flex-wrap">
                <h1 className="text-2xl sm:text-3xl font-bold text-foreground tracking-tight">{result.name}</h1>
                <span className="px-2 py-0.5 bg-primary text-primary-foreground text-[10px] font-semibold rounded uppercase tracking-wider">
                  {meta?.eps ?? result.groups.length} eps
                </span>
              </div>

              {/* Metadata row */}
              <div className="flex flex-wrap items-center gap-x-6 gap-y-1.5 text-sm text-muted-foreground">
                <span>Mikan ID: <span className="font-medium text-foreground">{result.mikan_id}</span></span>
                <span>Bangumi ID: <span className="font-medium text-foreground">{result.bangumi_id}</span></span>
                {meta?.air_date && <span>开播日期: {meta.air_date}</span>}
              </div>

              {/* Rating — separate line, pink accent */}
              {meta && meta.rating > 0 && (() => {
                const m = meta;
                return (
                <span className="shrink-0 text-[10px] font-semibold px-2 py-1 rounded-full bg-accent/15 text-accent inline-flex items-center gap-1 w-fit">
                  <svg className="w-3.5 h-3.5 fill-current shrink-0" viewBox="0 0 24 24">
                    <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14l-5-4.87 6.91-1.01L12 2z"/>
                  </svg>
                  {m.rating.toFixed(1)}
                  {m.rating_total > 0 && ` (${m.rating_total})`}
                </span>
                );
              })()}

              {/* Global RSS */}
              <div className="inline-flex items-center gap-2 mt-1 px-3 py-1.5 bg-background/60 border border-border rounded-lg text-xs max-w-fit">
                <svg className="w-4 h-4 text-primary/60 shrink-0" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M6.18 15.64a2.18 2.18 0 0 1 2.18 2.18C8.36 19 7.38 20 6.18 20 5 20 4 19 4 17.82a2.18 2.18 0 0 1 2.18-2.18M4 4.44A15.56 15.56 0 0 1 19.56 20h-2.83A12.73 12.73 0 0 0 4 7.27V4.44m0 5.66a9.9 9.9 0 0 1 9.9 9.9h-2.83A7.07 7.07 0 0 0 4 12.93V10.1z"/>
                </svg>
                <span className="text-muted-foreground">Global RSS:</span>
                <a href={result.global_rss} target="_blank" rel="noreferrer" className="text-primary font-medium hover:underline truncate max-w-[200px]">
                  {result.global_rss}
                </a>
                <button
                  className="p-0.5 hover:bg-muted rounded transition-colors cursor-pointer shrink-0"
                  onClick={(e) => {
                    e.preventDefault();
                    navigator.clipboard.writeText(result.global_rss).catch(() => {});
                  }}
                  title="Copy RSS URL"
                >
                  <svg className="w-3 h-3 text-muted-foreground" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2"/>
                  </svg>
                </button>
              </div>
            </div>
          </div>
        </header>

        {/* Scrollable group list */}
        <div className="flex-1 overflow-y-auto">
          <SubtitleGroupTable
            result={result}
            subscriptions={subscriptions}
            expanded={expanded}
            loadingFeed={loadingFeed}
            filterTags={filterTags}
            tagBoxOpen={tagBoxOpen}
            onToggleFeed={onToggleFeed}
            onToggleTag={onToggleTag}
            onToggleTagBox={onToggleTagBox}
            onSubscribe={onSubscribe}
            getSubMode={getSubMode}
            subscribingId={subscribingId}
            excludePatterns={excludePatterns}
            onExcludeChange={onExcludeChange}
          />
        </div>

        {/* Footer */}
        <footer className="px-5 py-3 border-t border-border bg-muted/20 flex justify-between items-center shrink-0">
          <div className="flex items-center gap-3">
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground font-semibold">
              {result.groups.length} groups
            </span>
            {(() => {
              const url = Object.keys(expanded).find(k => expanded[k] && expanded[k] !== null);
              if (!url || !expanded[url] || expanded[url] === null) return null;
              const items = (expanded[url] as RssFeedResponse).items;
              // Find which subgroup this feed belongs to for filtering
              const subgroupId = result.groups.find(g => g.rss_url === url)?.subgroup_id;
              const tags = subgroupId ? (filterTags[subgroupId] || []) : [];
              const keywords = subgroupId ? (excludePatterns[subgroupId] || []) : [];
              const filtered = items.filter(i => {
                if (i.excluded) return false;
                if (keywords.length > 0 && keywords.some(k => (i.guid || i.title).includes(k))) return false;
                if (tags.length > 0 && !tags.every(t => i.tags.includes(t))) return false;
                return true;
              });
              return filtered.length > 0 ? (
                <>
                  <span className="text-muted-foreground/30">|</span>
                  <span className="text-[10px] uppercase tracking-wider text-primary font-semibold">
                    可用条目 ({filtered.length}/{items.length})
                  </span>
                </>
              ) : null;
            })()}
          </div>
          <button
            className="bg-primary text-primary-foreground px-5 py-2 rounded-lg text-xs font-semibold hover:brightness-110 active:scale-95 transition-all cursor-pointer"
            onClick={onClose}
          >
            Close
          </button>
        </footer>
      </div>
    </div>
  );
}
