import { useState } from 'react';
import type { RssFeedResponse } from '@/types/preview';
import * as rssApi from '@/api/rssApi';
import { showLoadingToast, updateToast } from '@/lib/toast';
import RssSearchBar from '@/components/rss/RssSearchBar';
import SubtitleGroupDialog from '@/components/rss/SubtitleGroupDialog';
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
  const [excludePatterns, setExcludePatterns] = useState<Record<number, string[]>>({});

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

  const handleExcludeChange = async (subgroupId: number, patterns: string[], rssUrl: string) => {
    setExcludePatterns(prev => ({ ...prev, [subgroupId]: patterns }));
  };

  const handleExcludeBlur = async (subgroupId: number, rssUrl: string) => {
    // Refetch feed on blur to recalculate status
    setLoadingFeed(prev => ({ ...prev, [rssUrl]: true }));
    try {
      const feed = await rssApi.fetchRssFeed(rssUrl, {
        tags: filterTags[subgroupId] || [],
        excludePatterns: excludePatterns[subgroupId] || [],
      });
      setExpanded(prev => ({ ...prev, [rssUrl]: feed }));
    } catch { /* keep old feed */ }
    finally { setLoadingFeed(prev => { const n = { ...prev }; delete n[rssUrl]; return n; }); }
  };

  const doSubscribe = async (group: { name: string; subgroup_id: number; rss_url: string }, role: 'primary' | 'backup') => {
    if (!result) return;
    setSubscribingId(group.subgroup_id);
    const toastId = showLoadingToast("订阅中...");
    try {
      await subscribe(result, group, role, filterTags, excludePatterns, (msg) => {
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

      {result && (
        <SubtitleGroupDialog
          result={result}
          subscriptions={subscriptions}
          expanded={expanded}
          loadingFeed={loadingFeed}
          filterTags={filterTags}
          tagBoxOpen={tagBoxOpen}
          subscribingId={subscribingId}
          excludePatterns={excludePatterns}
          onToggleFeed={toggleFeed}
          onToggleTag={toggleTag}
          onToggleTagBox={(id) => setTagBoxOpen(prev => ({ ...prev, [id]: !prev[id] }))}
          onSubscribe={doSubscribe}
          onExcludeChange={handleExcludeChange}
          onExcludeBlur={handleExcludeBlur}
          getSubMode={getSubMode}
          onClose={clearSearch}
        />
      )}

      <div className="mt-8">
        <SubscriptionList
          subscriptions={subscriptions}
          loading={subLoading}
          onOpenHistory={openHistory}
          onUnsubscribe={(_bangumiId, sub) => setUnsubTarget(sub)}
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
