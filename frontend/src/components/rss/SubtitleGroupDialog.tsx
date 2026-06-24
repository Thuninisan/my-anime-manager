import type { BangumiRssResponse, RssFeedResponse, SubscriptionOut } from '@/types/preview';
import SubtitleGroupTable from './SubtitleGroupTable';

interface Props {
  result: BangumiRssResponse;
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
  onExcludeChange: (subgroupId: number, patterns: string[], rssUrl: string) => void;
  onExcludeBlur: (subgroupId: number, rssUrl: string) => void;
  getSubMode: (subgroupId: number) => 'primary' | 'backup' | null;
  onClose: () => void;
}

export default function SubtitleGroupDialog({
  result, subscriptions, expanded, loadingFeed, filterTags, tagBoxOpen,
  subscribingId, excludePatterns,
  onToggleFeed, onToggleTag, onToggleTagBox, onSubscribe,
  onExcludeChange, onExcludeBlur,
  getSubMode, onClose,
}: Props) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-md p-4">
      <div className="bg-card w-full max-w-5xl h-[85vh] rounded-xl shadow-2xl overflow-hidden flex flex-col border border-border">
        {/* Header */}
        <header className="px-5 py-4 border-b border-border bg-muted/20 flex justify-between items-start shrink-0">
          <div className="space-y-2">
            <div className="flex items-center gap-3">
              <div className="bg-primary/10 p-2 rounded-lg">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-primary">
                  <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M23 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" />
                </svg>
              </div>
              <div>
                <h2 className="text-lg font-semibold text-foreground leading-tight">{result.name}</h2>
                <div className="flex items-center gap-3 mt-1">
                  <span className="text-[10px] font-bold bg-muted px-2 py-0.5 rounded text-muted-foreground">
                    Mikan ID: {result.mikan_id}
                  </span>
                  <span className="text-xs text-muted-foreground">{result.groups.length} groups available</span>
                </div>
              </div>
            </div>
            {/* Global RSS */}
            <div className="flex items-center gap-1 text-xs">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-primary shrink-0">
                <path d="M4 11a9 9 0 0 1 9 9" /><path d="M4 4a16 16 0 0 1 16 16" /><circle cx="5" cy="19" r="1" />
              </svg>
              <span className="text-muted-foreground truncate max-w-lg">Global RSS: <a href={result.global_rss} target="_blank" rel="noreferrer" className="text-primary font-medium">{result.global_rss}</a></span>
            </div>
          </div>
          <button
            className="p-2 hover:bg-muted rounded-full transition-colors cursor-pointer shrink-0"
            onClick={onClose}
            aria-label="Close"
          >
            <svg className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
              <path d="M6 18L18 6M6 6l12 12" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
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
            onExcludeBlur={onExcludeBlur}
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
              const passed = items.filter(i => i.passed && !i.excluded).length;
              return passed > 0 ? (
                <>
                  <span className="text-muted-foreground/30">|</span>
                  <span className="text-[10px] uppercase tracking-wider text-primary font-semibold">
                    可用条目 ({passed}/{items.length})
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
