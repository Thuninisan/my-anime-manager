import { useState } from 'react';
import type { RssFeedResponse } from '@/types/preview';
import * as rssApi from '@/api/rssApi';
import { showError } from '@/lib/toast';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import RssSearchBar from '@/components/rss/RssSearchBar';
import SubtitleGroupTable from '@/components/rss/SubtitleGroupTable';
import SubscriptionList from '@/components/rss/SubscriptionList';
import DownloadHistoryDialog from '@/components/rss/DownloadHistoryDialog';
import { useRssSearch } from '@/hooks/useRssSearch';
import { useSubscriptions } from '@/hooks/useSubscriptions';
import { useDownloadHistory } from '@/hooks/useDownloadHistory';

export default function RssPage() {
  /* ── Hooks ── */
  const [bangumiId, setBangumiId] = useState('');
  const { result, searching, error: searchError, search } = useRssSearch();
  const { subscriptions, loading: subLoading, subscribe, unsubscribe, activate } = useSubscriptions();
  const { open: historyOpen, data: historyData, loading: historyLoading, openHistory, closeHistory } = useDownloadHistory();

  const handleSearch = () => search(bangumiId);

  /* ── Feed expansion (UI state, stays in page) ── */
  const [expanded, setExpanded] = useState<Record<string, RssFeedResponse | null>>({});
  const [loadingFeed, setLoadingFeed] = useState<Record<string, boolean>>({});

  /* ── Tag filter (UI state, stays in page) ── */
  const [filterTags, setFilterTags] = useState<Record<number, string[]>>({});
  const [tagBoxOpen, setTagBoxOpen] = useState<Record<number, boolean>>({});

  /* ── Feed toggle ── */
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

  /* ── Tag toggle ── */
  const toggleTag = (subgroupId: number, tag: string) => {
    setFilterTags(prev => {
      const cur = prev[subgroupId] || [];
      return { ...prev, [subgroupId]: cur.includes(tag) ? cur.filter(t => t !== tag) : [...cur, tag] };
    });
  };

  /* ── Subscribe wrapper ── */
  const doSubscribe = async (group: { name: string; subgroup_id: number; rss_url: string }, role: 'primary' | 'backup') => {
    if (!result) return;
    try {
      await subscribe(result, group, role, filterTags);
    } catch (e) { showError(e); }
  };

  /* ── Sub mode check ── */
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
    <div className="max-w-3xl mx-auto flex flex-col gap-4">
      <RssSearchBar
        bangumiId={bangumiId}
        searching={searching}
        searchError={searchError}
        onBangumiIdChange={setBangumiId}
        onSearch={handleSearch}
      />

      {result && (
        <Card>
          <CardHeader>
            <CardTitle>
              📺 字幕组列表
              <span className="ml-3 text-sm font-normal text-muted-foreground">Mikan ID: {result.mikan_id}</span>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex items-center gap-2 mb-3 p-2 bg-muted rounded-md text-sm">
              <span className="text-muted-foreground shrink-0">全局 RSS:</span>
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
          </CardContent>
        </Card>
      )}

      <SubscriptionList
        subscriptions={subscriptions}
        loading={subLoading}
        onOpenHistory={openHistory}
        onUnsubscribe={unsubscribe}
        onActivate={activate}
      />

      <DownloadHistoryDialog
        open={historyOpen}
        data={historyData}
        loading={historyLoading}
        onClose={closeHistory}
      />
    </div>
  );
}
