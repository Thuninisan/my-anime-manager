import { useState } from 'react';
import type { RssFeedResponse } from '@/types/preview';
import * as rssApi from '@/api/rssApi';
import { showLoadingToast, updateToast } from '@/lib/toast';
import RssSearchBar from '@/components/rss/RssSearchBar';
import SubtitleGroupDialog from '@/components/rss/SubtitleGroupDialog';
import MikanSearchDialog from '@/components/rss/MikanSearchDialog';
import SubscriptionList from '@/components/rss/SubscriptionList';
import DownloadHistoryDialog from '@/components/rss/DownloadHistoryDialog';
import UnsubscribeDialog from '@/components/rss/UnsubscribeDialog';
import TmdbSearchDialog from '@/components/rss/TmdbSearchDialog';
import { useRssSearch } from '@/hooks/useRssSearch';
import { useSubscriptions } from '@/hooks/useSubscriptions';
import { useDownloadHistory } from '@/hooks/useDownloadHistory';

export default function RssPage() {
  const [bangumiId, setBangumiId] = useState('');
  const { result, meta, searching, error: searchError, search, clear: clearSearch, setExternalResult } = useRssSearch();
  const { subscriptions, loading: subLoading, subscribe, unsubscribe, activate, refresh: refreshSubs } = useSubscriptions();
  const { open: historyOpen, data: historyData, loading: historyLoading, subscription: historySub, openHistory, closeHistory, refreshHistory } = useDownloadHistory();

  const [expanded, setExpanded] = useState<Record<string, RssFeedResponse | null>>({});
  const [loadingFeed, setLoadingFeed] = useState<Record<string, boolean>>({});
  const [filterTags, setFilterTags] = useState<Record<number, string[]>>({});
  const [tagBoxOpen, setTagBoxOpen] = useState<Record<number, boolean>>({});
  const [unsubTarget, setUnsubTarget] = useState<import('@/types/preview').SubscriptionOut | null>(null);
  const [subscribingId, setSubscribingId] = useState<number | null>(null);
  const [excludePatterns, setExcludePatterns] = useState<Record<number, string[]>>({});

  // Mikan fallback state — opened when search result has no mikan_id
  const [mikanFallback, setMikanFallback] = useState<{ bangumi_id: number; name: string } | null>(null);
  const [mikanMeta, setMikanMeta] = useState<import('@/types/preview').BangumiMeta | null>(null);

  // TMDB manual override state — Tier-2 fallback when auto-inference fails
  const [tmdbDialog, setTmdbDialog] = useState<{ bangumi_id: number; name: string } | null>(null);

  const handleSearch = (id: number, candidate?: { has_mikan_id: boolean; name: string }) => {
    // Entry has bangumi_id but no mikan_id → trigger Mikan search fallback
    if (candidate && !candidate.has_mikan_id) {
      setMikanFallback({ bangumi_id: id, name: candidate.name });
      // Fetch Bangumi meta in parallel for display in the dialog
      rssApi.getBangumiMeta(id).then(setMikanMeta).catch(() => setMikanMeta(null));
      return;
    }
    search(String(id));
  };

  const handleMikanAssigned = (rssResult: import('@/types/preview').BangumiRssResponse) => {
    setExternalResult(rssResult, mikanMeta);
    setMikanFallback(null);
  };

  const handleManualSubscribed = () => {
    setMikanFallback(null);
    refreshSubs();
  };

  const handleSetTmdb = (bangumiId: number, name: string) => {
    setTmdbDialog({ bangumi_id: bangumiId, name });
  };

  const handleTmdbAssigned = (_tmdbId: number, _tmdbSeason: number | null) => {
    setTmdbDialog(null);
    refreshSubs();
  };

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

  const handleExcludeChange = (subgroupId: number, patterns: string[]) => {
    setExcludePatterns(prev => ({ ...prev, [subgroupId]: patterns }));
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

  const handleDeleteGroupRss = async (type: 'primary' | 'backup') => {
    if (!result) return;
    try {
      await rssApi.deleteSubscriptionRss(result.bangumi_id, type);
      await refreshSubs();
    } catch { /* */ }
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

  // Which roles are already taken by ANY subgroup for the current search result?
  const takenRoles = (() => {
    if (!result) return { primary: false, backup: false };
    let primary = false, backup = false;
    for (const s of subscriptions) {
      if (s.bangumi_id !== result.bangumi_id) continue;
      if (s.subgroup_id) primary = true;
      if (s.backup_subgroup_id) backup = true;
    }
    return { primary, backup };
  })();

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
          meta={meta}
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
          getSubMode={getSubMode}
          takenRoles={takenRoles}
          onDeleteRss={handleDeleteGroupRss}
          onClose={clearSearch}
        />
      )}

      {mikanFallback && (
        <MikanSearchDialog
          open={true}
          bangumiId={mikanFallback.bangumi_id}
          bangumiName={mikanFallback.name}
          meta={mikanMeta}
          onClose={() => { setMikanFallback(null); setMikanMeta(null); }}
          onMikanAssigned={handleMikanAssigned}
          onManualSubscribed={handleManualSubscribed}
        />
      )}

      <div className="mt-8">
        <SubscriptionList
          subscriptions={subscriptions}
          loading={subLoading}
          onOpenHistory={openHistory}
          onUnsubscribe={(_bangumiId, sub) => setUnsubTarget(sub)}
          onActivate={activate}
          onSetTmdb={handleSetTmdb}
        />
      </div>

      <DownloadHistoryDialog
        open={historyOpen}
        data={historyData}
        loading={historyLoading}
        subscription={historySub}
        onClose={closeHistory}
        onRefresh={refreshHistory}
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

      {tmdbDialog && (
        <TmdbSearchDialog
          open={true}
          bangumiId={tmdbDialog.bangumi_id}
          bangumiName={tmdbDialog.name}
          onClose={() => setTmdbDialog(null)}
          onAssigned={handleTmdbAssigned}
        />
      )}
    </>
  );
}
