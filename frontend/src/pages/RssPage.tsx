import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import type { RssFeedResponse } from '@/types/preview';
import * as rssApi from '@/api/rssApi';
import { bangumiSearchItem } from '@/api/searchApi';
import { showLoadingToast, updateToast } from '@/lib/toast';
import RssSearchBar from '@/components/rss/RssSearchBar';
import SearchResultDialog from '@/components/shared/SearchResultDialog';
import SubtitleGroupDialog from '@/components/rss/SubtitleGroupDialog';
import MikanSearchDialog from '@/components/rss/MikanSearchDialog';
import SubscriptionList from '@/components/rss/SubscriptionList';
import DownloadHistoryDialog from '@/components/rss/DownloadHistoryDialog';
import UnsubscribeDialog from '@/components/rss/UnsubscribeDialog';
import MappingEditDialog from '@/components/rss/MappingEditDialog';
import type { BangumiMapping } from '@/types/mapping';
import { toast } from 'sonner';
import { useRssSearch } from '@/hooks/useRssSearch';
import { useSubscriptions } from '@/hooks/useSubscriptions';
import { useDownloadHistory } from '@/hooks/useDownloadHistory';

export default function RssPage() {
  const [bangumiId, setBangumiId] = useState('');
  const [onlineSearch, setOnlineSearch] = useState<{ query: string; results: rssApi.BangumiOnlineResult[]; loading: boolean; error: string } | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const [entryError, setEntryError] = useState('');
  const { result, meta, mikanFallback, searching, error: searchError, search, clear: clearSearch, setExternalResult } = useRssSearch();
  const { subscriptions, loading: subLoading, subscribe, unsubscribe, activate, refresh: refreshSubs } = useSubscriptions();
  const { open: historyOpen, data: historyData, loading: historyLoading, subscription: historySub, openHistory, closeHistory, refreshHistory } = useDownloadHistory();

  const [expanded, setExpanded] = useState<Record<string, RssFeedResponse | null>>({});
  const [loadingFeed, setLoadingFeed] = useState<Record<string, boolean>>({});
  const [filterTags, setFilterTags] = useState<Record<number, string[]>>({});
  const [tagBoxOpen, setTagBoxOpen] = useState<Record<number, boolean>>({});
  const [unsubTarget, setUnsubTarget] = useState<import('@/types/preview').SubscriptionOut | null>(null);
  const [subscribingId, setSubscribingId] = useState<number | null>(null);
  const [excludePatterns, setExcludePatterns] = useState<Record<number, string[]>>({});

  // 订阅和字幕组共用映射编辑入口。
  const [mappingDialog, setMappingDialog] = useState<{ bangumi_id: number; name: string } | null>(null);

  const handleSearch = (id: number, candidate?: { has_mikan_id: boolean; name: string }) => {
    setEntryError('');
    void search(String(id), candidate);
  };

  useEffect(() => {
    const id = searchParams.get('bangumi_id');
    if (!id || !/^[1-9]\d*$/.test(id)) return;
    let active = true;
    setBangumiId(id);
    setEntryError('');
    const name = searchParams.get('name');
    const openEntry = async () => {
      try {
        const candidate = name ? await rssApi.saveBangumiSelection({
          bangumi_id: Number(id), name,
          name_original: searchParams.get('name_original') || '', date: '', poster_url: '',
        }) : undefined;
        if (!active) return;
        // Consume the navigation request so subsequent RSS searches are independent.
        setSearchParams({}, { replace: true });
        void search(id, candidate);
      } catch (e) {
        if (active) setEntryError(e instanceof Error ? e.message : '打开订阅条目失败');
      }
    };
    void openEntry();
    return () => { active = false; };
  }, [searchParams, setSearchParams, search]);

  const handleOnlineSearch = async (query: string) => {
    setOnlineSearch({ query, results: [], loading: true, error: '' });
    try {
      const results = await rssApi.searchBangumiOnline(query);
      setOnlineSearch(current => current?.query === query ? { query, results, loading: false, error: '' } : current);
    } catch (e) {
      setOnlineSearch(current => current?.query === query ? { query, results: [], loading: false, error: e instanceof Error ? e.message : 'Bangumi 搜索失败' } : current);
    }
  };

  const handleOnlineSelect = async (item: rssApi.BangumiOnlineResult) => {
    try {
      const candidate = await rssApi.saveBangumiSelection(item);
      setOnlineSearch(null);
      setBangumiId(String(item.bangumi_id));
      handleSearch(item.bangumi_id, candidate);
    } catch (e) {
      setOnlineSearch(current => current ? { ...current, error: e instanceof Error ? e.message : '保存条目失败' } : current);
    }
  };

  const handleMikanAssigned = (rssResult: import('@/types/preview').BangumiRssResponse) => {
    setExternalResult(rssResult, meta);
  };

  const handleManualSubscribed = () => {
    clearSearch();
    refreshSubs();
  };

  const handleEditMapping = (bangumiId: number, name: string) => {
    setMappingDialog({ bangumi_id: bangumiId, name });
  };

  const handleMappingSaved = (saved: BangumiMapping, previous: BangumiMapping, close = true) => {
    if (close) setMappingDialog(null);
    toast.success('映射已保存，后续匹配和下载将使用新映射');
    void refreshSubs();
    if (saved.mikan_id !== previous.mikan_id) {
      // 重新读取新来源，避免继续展示和选择旧来源的字幕组。
      setExpanded({});
      setFilterTags({});
      setTagBoxOpen({});
      setExcludePatterns({});
      void search(String(saved.bangumi_id), { has_mikan_id: saved.mikan_id !== null, name: mappingDialog?.name || result?.name || String(saved.bangumi_id) });
    }
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
      if (s.primary.subgroup_id === subgroupId) return 'primary';
      if (s.backup.subgroup_id === subgroupId) return 'backup';
    }
    return null;
  };

  // Which roles are already taken by ANY subgroup for the current search result?
  const takenRoles = (() => {
    if (!result) return { primary: false, backup: false };
    let primary = false, backup = false;
    for (const s of subscriptions) {
      if (s.bangumi_id !== result.bangumi_id) continue;
      if (s.primary.subgroup_id) primary = true;
      if (s.backup.subgroup_id) backup = true;
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
          searchError={entryError || searchError}
          onBangumiIdChange={setBangumiId}
          onSearch={handleSearch}
          onOnlineSearch={handleOnlineSearch}
        />
      </div>

      {onlineSearch && <SearchResultDialog {...onlineSearch} provider="bangumi" results={onlineSearch.results.map(bangumiSearchItem)}
        onClose={() => setOnlineSearch(null)} onSelect={item => { void handleOnlineSelect({ ...item, bangumi_id: item.provider_id }); }} />}

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
          onEditMapping={handleEditMapping}
        />
      )}

      {mikanFallback && (
        <MikanSearchDialog
          open={true}
          bangumiId={mikanFallback.bangumi_id}
          bangumiName={mikanFallback.name}
          meta={meta}
          onClose={clearSearch}
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
          onEditMapping={handleEditMapping}
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

      {mappingDialog && (
        <MappingEditDialog
          key={mappingDialog.bangumi_id}
          bangumiId={mappingDialog.bangumi_id}
          name={mappingDialog.name}
          isMovie={subscriptions.find(sub => sub.bangumi_id === mappingDialog.bangumi_id)?.resource_identity.media_type === 'movie'}
          onClose={() => setMappingDialog(null)}
          onSaved={handleMappingSaved}
        />
      )}
    </>
  );
}
