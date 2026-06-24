import { useState } from 'react';
import type { RssFeedResponse } from '@/types/preview';
import * as rssApi from '@/api/rssApi';
import { showError, showLoadingToast, updateToast } from '@/lib/toast';
import RssSearchBar from '@/components/rss/RssSearchBar';
import SubtitleGroupTable from '@/components/rss/SubtitleGroupTable';
import SubscriptionList from '@/components/rss/SubscriptionList';
import DownloadHistoryDialog from '@/components/rss/DownloadHistoryDialog';
import UnsubscribeDialog from '@/components/rss/UnsubscribeDialog';
import { useRssSearch } from '@/hooks/useRssSearch';
import { useSubscriptions } from '@/hooks/useSubscriptions';
import { useDownloadHistory } from '@/hooks/useDownloadHistory';

export default function RssPage() {
  const [bangumiId, setBangumiId] = useState('');
  const { result, searching, error: searchError, search, clear: clearSearch } = useRssSearch();
  const { subscriptions, loading: subLoading, subscribe, unsubscribe, activate } = useSubscriptions();
  const { open: historyOpen, data: historyData, loading: historyLoading, subscription: historySub, openHistory, closeHistory } = useDownloadHistory();

  const [expanded, setExpanded] = useState<Record<string, RssFeedResponse | null>>({});
  const [loadingFeed, setLoadingFeed] = useState<Record<string, boolean>>({});
  const [filterTags, setFilterTags] = useState<Record<number, string[]>>({});
  const [tagBoxOpen, setTagBoxOpen] = useState<Record<number, boolean>>({});
  const [unsubTarget, setUnsubTarget] = useState<import('@/types/preview').SubscriptionOut | null>(null);
  const [subscribingId, setSubscribingId] = useState<number | null>(null);

  const handleSearch = () => search(bangumiId);

  const toggleFeed = async (rssUrl: string) => {
    if (expanded[rssUrl] !== undefined) {
      setExpanded(prev => { const n = { ...prev }; delete n[rssUrl]; return n; });
      return;
    }
    setLoadingFeed(prev => ({ ...prev, [rssUrl]: true }));
    try {
      const feed = await rssApi.fetchRssFeed(rssUrl);
      setExpanded(prev => ({ ...prev, [rssUrl]: feed }));
    } catch { setExpanded(prev => ({ ...prev, [rssUrl]: null })); }
    finally { setLoadingFeed(prev => { const n = { ...prev }; delete n[rssUrl]; return n; }); }
  };

  const toggleTag = (subgroupId: number, tag: string) => {
    setFilterTags(prev => {
      const cur = prev[subgroupId] || [];
      return { ...prev, [subgroupId]: cur.includes(tag) ? cur.filter(t => t !== tag) : [...cur, tag] };
    });
  };

  const doSubscribe = async (group: { name: string; subgroup_id: number; rss_url: string }, role: 'primary' | 'backup') => {
    if (!result) return;
    setSubscribingId(group.subgroup_id);
    const toastId = showLoadingToast("订阅中...");
    try {
      await subscribe(result, group, role, filterTags, (msg) => {
        updateToast(toastId, msg, "loading");
      });
      updateToast(toastId, `✅ ${group.name} 订阅完成`, "success");
    } catch (e) {
      updateToast(toastId, `❌ ${group.name}: ${e instanceof Error ? e.message : String(e)}`, "error");
    } finally {
      setSubscribingId(null);
    }
  };

  const getSubMode = (subgroupId: number): 'primary' | 'backup' | null => {
    if (!result) return null;
    for (const s of subscriptions) {
      if (s.bangumi_id !== result.bangumi_id) continue;
      if (s.subgroup_id === subgroupId) return 'primary';
      if (s.backup_subgroup_id === subgroupId) return 'backup';
    }
    return null;
  };

  return (
    <>
      {/* Header: title + search */}
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-6 pb-2">
        <div className="space-y-1">
          <h2 className="text-2xl font-bold text-foreground">My Subscriptions</h2>
          <p className="text-sm text-muted-foreground">
            Managing {subscriptions.length} active automated download{subscriptions.length !== 1 ? 's' : ''}
          </p>
        </div>
        <RssSearchBar
          bangumiId={bangumiId}
          searching={searching}
          searchError={searchError}
          onBangumiIdChange={setBangumiId}
          onSearch={handleSearch}
        />
      </div>

      {/* Search results dialog */}
      {result && (
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
                <div className="flex items-center gap-1 text-xs group/ml-0">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-primary shrink-0">
                    <path d="M4 11a9 9 0 0 1 9 9" /><path d="M4 4a16 16 0 0 1 16 16" /><circle cx="5" cy="19" r="1" />
                  </svg>
                  <span className="text-muted-foreground truncate max-w-lg">Global RSS: <a href={result.global_rss} target="_blank" rel="noreferrer" className="text-primary font-medium">{result.global_rss}</a></span>
                </div>
              </div>
              <button
                className="p-2 hover:bg-muted rounded-full transition-colors cursor-pointer shrink-0"
                onClick={clearSearch}
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
                onToggleFeed={toggleFeed}
                onToggleTag={toggleTag}
                onToggleTagBox={(id) => setTagBoxOpen(prev => ({ ...prev, [id]: !prev[id] }))}
                onSubscribe={doSubscribe}
                getSubMode={getSubMode}
                subscribingId={subscribingId}
              />
            </div>

            {/* Footer */}
            <footer className="px-5 py-3 border-t border-border bg-muted/20 flex justify-between items-center shrink-0">
              <div className="flex items-center gap-3">
                {(() => {
                  const url = Object.keys(expanded).find(k => expanded[k] && expanded[k] !== null);
                  const count = url && expanded[url] && expanded[url] !== null
                    ? (expanded[url] as RssFeedResponse).items.filter(i => i.passed && !i.excluded).length
                    : 0;
                  return count > 0 ? (
                    <span className="text-[10px] uppercase tracking-wider text-muted-foreground font-semibold">
                      Recent Entries ({count} items)
                    </span>
                  ) : (
                    <span className="text-[10px] uppercase tracking-wider text-muted-foreground font-semibold">
                      {result.groups.length} groups
                    </span>
                  );
                })()}
              </div>
              <button
                className="bg-primary text-primary-foreground px-5 py-2 rounded-lg text-xs font-semibold hover:brightness-110 active:scale-95 transition-all cursor-pointer"
                onClick={clearSearch}
              >
                Close
              </button>
            </footer>
          </div>
        </div>
      )}

      {/* Subscription grid */}
      <div className="mt-8">
        <SubscriptionList
          subscriptions={subscriptions}
          loading={subLoading}
          onOpenHistory={openHistory}
          onUnsubscribe={(bangumiId, sub) => setUnsubTarget(sub)}
          onActivate={activate}
        />
      </div>

      <DownloadHistoryDialog
        open={historyOpen}
        data={historyData}
        loading={historyLoading}
        subscription={historySub}
        onClose={closeHistory}
      />

      <UnsubscribeDialog
        open={unsubTarget !== null}
        subscription={unsubTarget}
        onClose={() => setUnsubTarget(null)}
        onConfirm={async (bangumiId, deleteFiles) => {
          await unsubscribe(bangumiId, deleteFiles);
          setUnsubTarget(null);
        }}
      />
    </>
  );
}
