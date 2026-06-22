import { useState, useEffect, useCallback } from 'react';
import type { BangumiRssResponse, RssFeedResponse, SubscriptionIn, SubscriptionOut } from '@/types/preview';
import type { DownloadHistoryResponse } from '@/types/preview';
import * as rssApi from '@/api/rssApi';
import { showError } from '@/lib/toast';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import RssSearchBar from '@/components/rss/RssSearchBar';
import SubtitleGroupTable from '@/components/rss/SubtitleGroupTable';
import SubscriptionList from '@/components/rss/SubscriptionList';
import DownloadHistoryDialog from '@/components/rss/DownloadHistoryDialog';

export default function RssPage() {
  /* ── Search state ── */
  const [bangumiId, setBangumiId] = useState('');
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState('');
  const [result, setResult] = useState<BangumiRssResponse | null>(null);

  /* ── Subscriptions ── */
  const [subscriptions, setSubscriptions] = useState<SubscriptionOut[]>([]);
  const [subLoading, setSubLoading] = useState(false);

  /* ── Feed expansion ── */
  const [expanded, setExpanded] = useState<Record<string, RssFeedResponse | null>>({});
  const [loadingFeed, setLoadingFeed] = useState<Record<string, boolean>>({});

  /* ── Tag filter ── */
  const [filterTags, setFilterTags] = useState<Record<number, string[]>>({});
  const [tagBoxOpen, setTagBoxOpen] = useState<Record<number, boolean>>({});

  /* ── History ── */
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyData, setHistoryData] = useState<DownloadHistoryResponse | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyBangumiId, setHistoryBangumiId] = useState(0);

  /* ── Load subscriptions ── */
  const loadSubs = useCallback(async () => {
    setSubLoading(true);
    try { setSubscriptions(await rssApi.listSubscriptions()); } catch { /* */ }
    finally { setSubLoading(false); }
  }, []);
  useEffect(() => { loadSubs(); }, [loadSubs]);

  /* ── Search ── */
  const handleSearch = async () => {
    const id = parseInt(bangumiId.trim(), 10);
    if (!id || id <= 0) { setSearchError('请输入有效的 Bangumi ID'); return; }
    setSearching(true); setSearchError(''); setResult(null);
    try { setResult(await rssApi.lookupBangumiRss(id)); }
    catch (e: unknown) { setSearchError(e instanceof Error ? e.message : 'Search failed'); showError(e); }
    finally { setSearching(false); }
  };

  /* ── Subscribe ── */
  const doSubscribe = async (group: { name: string; subgroup_id: number; rss_url: string }, role: 'primary' | 'backup') => {
    if (!result) return;
    const tags = filterTags[group.subgroup_id] || [];
    const existing = subscriptions.find(s => s.bangumi_id === result.bangumi_id);
    let body: SubscriptionIn;
    if (role === 'primary') {
      body = {
        name: result.name, rss_url: group.rss_url, bangumi_id: result.bangumi_id,
        subgroup_id: group.subgroup_id, subgroup_name: group.name, filter_tags: tags,
        backup_rss_url: existing?.rss_url || '', backup_subgroup_id: existing?.subgroup_id || 0,
        backup_subgroup_name: existing?.subgroup_name || '', backup_filter_tags: existing?.filter_tags || [],
        download_path: existing?.download_path || '',
      };
    } else {
      body = {
        name: result.name, rss_url: existing?.rss_url || '', bangumi_id: result.bangumi_id,
        subgroup_id: existing?.subgroup_id || 0, subgroup_name: existing?.subgroup_name || '',
        filter_tags: existing?.filter_tags || [],
        backup_rss_url: group.rss_url, backup_subgroup_id: group.subgroup_id,
        backup_subgroup_name: group.name, backup_filter_tags: tags,
        download_path: existing?.download_path || '',
      };
    }
    try {
      const sub = await rssApi.createSubscription(body);
      setSubscriptions(prev => {
        const idx = prev.findIndex(s => s.bangumi_id === result.bangumi_id);
        if (idx >= 0) { const next = [...prev]; next[idx] = sub; return next; }
        return [...prev, sub];
      });
    } catch (e) { showError(e); }
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
    }
    catch { setExpanded(prev => ({ ...prev, [rssUrl]: null })); }
    finally { setLoadingFeed(prev => { const n = { ...prev }; delete n[rssUrl]; return n; }); }
  };

  /* ── Tag toggle ── */
  const toggleTag = (subgroupId: number, tag: string) => {
    setFilterTags(prev => {
      const cur = prev[subgroupId] || [];
      return { ...prev, [subgroupId]: cur.includes(tag) ? cur.filter(t => t !== tag) : [...cur, tag] };
    });
  };
  const toggleTagBox = (subgroupId: number) => {
    setTagBoxOpen(prev => ({ ...prev, [subgroupId]: !prev[subgroupId] }));
  };

  /* ── Unsubscribe / Activate ── */
  const unsubscribe = async (id: number) => {
    try { await rssApi.deleteSubscription(id); setSubscriptions(prev => prev.filter(s => s.bangumi_id !== id)); }
    catch { /* */ }
  };
  const activate = async (id: number) => {
    await rssApi.activateSubscription(id);
    await loadSubs();
  };

  /* ── History ── */
  const fetchHistory = async (bgmId: number) => {
    try { setHistoryData(await rssApi.getDownloadHistory(bgmId)); } catch { /* */ }
  };
  const openHistory = async (bgmId: number) => {
    setHistoryLoading(true); setHistoryOpen(true); setHistoryData(null); setHistoryBangumiId(bgmId);
    try { setHistoryData(await rssApi.getDownloadHistory(bgmId)); } catch { /* */ }
    finally { setHistoryLoading(false); }
  };
  useEffect(() => {
    if (!historyOpen || !historyBangumiId) return;
    const id = setInterval(() => fetchHistory(historyBangumiId), 5000);
    return () => clearInterval(id);
  }, [historyOpen, historyBangumiId]);

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
              onToggleTagBox={toggleTagBox}
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
        onClose={() => { setHistoryOpen(false); setHistoryBangumiId(0); }}
      />
    </div>
  );
}
