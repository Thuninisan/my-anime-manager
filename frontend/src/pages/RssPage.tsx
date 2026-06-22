import { useState } from 'react';
import type { RssFeedResponse } from '@/types/preview';
import * as rssApi from '@/api/rssApi';
import { showError } from '@/lib/toast';
import RssSearchBar from '@/components/rss/RssSearchBar';
import SubtitleGroupTable from '@/components/rss/SubtitleGroupTable';
import SubscriptionList from '@/components/rss/SubscriptionList';
import DownloadHistoryDialog from '@/components/rss/DownloadHistoryDialog';
import { useRssSearch } from '@/hooks/useRssSearch';
import { useSubscriptions } from '@/hooks/useSubscriptions';
import { useDownloadHistory } from '@/hooks/useDownloadHistory';

export default function RssPage() {
  const [bangumiId, setBangumiId] = useState('');
  const { result, searching, error: searchError, search } = useRssSearch();
  const { subscriptions, loading: subLoading, subscribe, unsubscribe, activate } = useSubscriptions();
  const { open: historyOpen, data: historyData, loading: historyLoading, openHistory, closeHistory } = useDownloadHistory();

  const [expanded, setExpanded] = useState<Record<string, RssFeedResponse | null>>({});
  const [loadingFeed, setLoadingFeed] = useState<Record<string, boolean>>({});
  const [filterTags, setFilterTags] = useState<Record<number, string[]>>({});
  const [tagBoxOpen, setTagBoxOpen] = useState<Record<number, boolean>>({});

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
    try { await subscribe(result, group, role, filterTags); }
    catch (e) { showError(e); }
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

      {/* Search results (subtitle groups) */}
      {result && (
        <div className="mt-6 glass-card rounded-xl sakura-shadow p-6 space-y-3">
          <h3 className="text-base font-semibold flex items-center gap-2">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="10"/><path d="M8.56 2.75c4.37 6.03 6.02 9.42 8.03 17.72m2.54-15.38c-3.72 4.35-8.94 5.66-13.88 5.66"/>
            </svg>
            Subtitle Groups
            <span className="ml-2 text-sm font-normal text-muted-foreground">Mikan ID: {result.mikan_id}</span>
          </h3>
          <div className="flex items-center gap-2 p-2 bg-muted/50 rounded-lg text-sm">
            <span className="text-muted-foreground shrink-0">Global RSS:</span>
            <a href={result.global_rss} target="_blank" rel="noreferrer" className="text-primary break-all">
              {result.global_rss}
            </a>
          </div>
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
          />
        </div>
      )}

      {/* Subscription grid */}
      <div className="mt-8">
        <SubscriptionList
          subscriptions={subscriptions}
          loading={subLoading}
          onOpenHistory={openHistory}
          onUnsubscribe={unsubscribe}
          onActivate={activate}
        />
      </div>

      <DownloadHistoryDialog
        open={historyOpen}
        data={historyData}
        loading={historyLoading}
        onClose={closeHistory}
      />
    </>
  );
}
