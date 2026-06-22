import { useState, useEffect, useCallback } from 'react';
import type { BangumiRssResponse, RssFeedResponse, SubscriptionIn, SubscriptionOut } from '../types/preview';
import * as rssApi from '../api/rssApi';
import { showError } from '../lib/toast';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { DropdownMenuRoot, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem } from '@/components/ui/dropdown-menu';
import { DialogRoot, DialogContent, DialogHeader, DialogTitle, DialogBody, DialogFooter, DialogClose } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';

const AVAILABLE_TAGS = ['简体', '繁体', '日语', '内封', '内嵌', '双语'];
const TAG_COLORS: Record<string, string> = {
  '简体': 'bg-sky-500/15 text-sky-600 dark:text-sky-400', '繁体': 'bg-purple-500/15 text-purple-600 dark:text-purple-400',
  '日语': 'bg-pink-500/15 text-pink-600 dark:text-pink-400', '内封': 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400',
  '内嵌': 'bg-amber-500/15 text-amber-600 dark:text-amber-400', '双语': 'bg-cyan-500/15 text-cyan-600 dark:text-cyan-400',
};

function RssManager() {
  const [bangumiId, setBangumiId] = useState('');
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState('');
  const [result, setResult] = useState<BangumiRssResponse | null>(null);
  const [subscriptions, setSubscriptions] = useState<SubscriptionOut[]>([]);
  const [subLoading, setSubLoading] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, RssFeedResponse | null>>({});
  const [loadingFeed, setLoadingFeed] = useState<Record<string, boolean>>({});
  const [filterTags, setFilterTags] = useState<Record<number, string[]>>({});
  const [tagBoxOpen, setTagBoxOpen] = useState<Record<number, boolean>>({});
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyData, setHistoryData] = useState<import('../types/preview').DownloadHistoryResponse | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyBangumiId, setHistoryBangumiId] = useState(0);

  const fetchHistory = async (bangumiId: number) => {
    try { setHistoryData(await rssApi.getDownloadHistory(bangumiId)); } catch { /* */ }
  };

  const openHistory = async (bangumiId: number) => {
    setHistoryLoading(true); setHistoryOpen(true); setHistoryData(null); setHistoryBangumiId(bangumiId);
    try { setHistoryData(await rssApi.getDownloadHistory(bangumiId)); } catch { /* */ }
    finally { setHistoryLoading(false); }
  };

  // Auto-refresh history every 5s while dialog is open
  useEffect(() => {
    if (!historyOpen || !historyBangumiId) return;
    const id = setInterval(() => fetchHistory(historyBangumiId), 5000);
    return () => clearInterval(id);
  }, [historyOpen, historyBangumiId]);

  const loadSubs = useCallback(async () => {
    setSubLoading(true);
    try { setSubscriptions(await rssApi.listSubscriptions()); } catch { /* */ }
    finally { setSubLoading(false); }
  }, []);
  useEffect(() => { loadSubs(); }, [loadSubs]);

  const handleSearch = async () => {
    const id = parseInt(bangumiId.trim(), 10);
    if (!id || id <= 0) { setSearchError('请输入有效的 Bangumi ID'); return; }
    setSearching(true); setSearchError(''); setResult(null);
    try { setResult(await rssApi.lookupBangumiRss(id)); }
    catch (e: unknown) { setSearchError(e instanceof Error ? e.message : 'Search failed'); showError(e); }
    finally { setSearching(false); }
  };
  const doSubscribe = async (group: { name: string; subgroup_id: number; rss_url: string }, role: 'primary' | 'backup') => {
    if (!result) return;
    const tags = filterTags[group.subgroup_id] || [];

    // Find existing subscription for this bangumi_id
    const existing = subscriptions.find(s => s.bangumi_id === result.bangumi_id);

    // Build the complete subscription JSON
    let body: SubscriptionIn;
    if (role === 'primary') {
      // This group becomes primary.  Demote previous primary to backup if needed.
      body = {
        name: result.name,
        rss_url: group.rss_url,
        bangumi_id: result.bangumi_id,
        subgroup_id: group.subgroup_id,
        subgroup_name: group.name,
        filter_tags: tags,
        backup_rss_url: existing?.rss_url || '',
        backup_subgroup_id: existing?.subgroup_id || 0,
        backup_subgroup_name: existing?.subgroup_name || '',
        backup_filter_tags: existing?.filter_tags || [],
        download_path: existing?.download_path || '',
      };
    } else {
      // This group becomes backup.  Keep existing primary as-is.
      body = {
        name: result.name,
        rss_url: existing?.rss_url || '',
        bangumi_id: result.bangumi_id,
        subgroup_id: existing?.subgroup_id || 0,
        subgroup_name: existing?.subgroup_name || '',
        filter_tags: existing?.filter_tags || [],
        backup_rss_url: group.rss_url,
        backup_subgroup_id: group.subgroup_id,
        backup_subgroup_name: group.name,
        backup_filter_tags: tags,
        download_path: existing?.download_path || '',
      };
    }

    try {
      const sub = await rssApi.createSubscription(body);
      setSubscriptions(prev => {
        const idx = prev.findIndex(s => s.bangumi_id === result.bangumi_id);
        if (idx >= 0) {
          const next = [...prev];
          next[idx] = sub;
          return next;
        }
        return [...prev, sub];
      });
    } catch (e) { showError(e); }
  };
  const unsubscribe = async (bangumiId: number) => {
    try { await rssApi.deleteSubscription(bangumiId); setSubscriptions(prev => prev.filter(s => s.bangumi_id !== bangumiId)); } catch { /* */ }
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
  const toggleFeed = async (rssUrl: string, subscriptionId?: string) => {
    if (expanded[rssUrl] !== undefined) { setExpanded(prev => { const n = { ...prev }; delete n[rssUrl]; return n; }); return; }
    setLoadingFeed(prev => ({ ...prev, [rssUrl]: true }));
    try {
      const feed = await rssApi.fetchRssFeed(rssUrl, { subscriptionId });
      setExpanded(prev => ({ ...prev, [rssUrl]: feed }));
    } catch { setExpanded(prev => ({ ...prev, [rssUrl]: null })); }
    finally { setLoadingFeed(prev => { const n = { ...prev }; delete n[rssUrl]; return n; }); }
  };
  const toggleTag = (subgroupId: number, tag: string) => {
    setFilterTags(prev => { const cur = prev[subgroupId] || []; return { ...prev, [subgroupId]: cur.includes(tag) ? cur.filter(t => t !== tag) : [...cur, tag] }; });
  };
  const toggleTagBox = (subgroupId: number) => {
    setTagBoxOpen(prev => ({ ...prev, [subgroupId]: !prev[subgroupId] }));
  };
  const formatSize = (bytes: number) => bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : bytes >= 1e6 ? `${(bytes / 1e6).toFixed(0)} MB` : `${(bytes / 1e3).toFixed(0)} KB`;
  const renderTags = (tags: string[]) => tags.map(t => <span key={t} className={`text-xs px-1.5 py-0.5 rounded ${TAG_COLORS[t] || 'bg-muted text-muted-foreground'}`}>{t}</span>);

  return (
    <div className="max-w-3xl mx-auto flex flex-col gap-4">
      {/* Search */}
      <Card>
        <CardContent className="pt-4">
          <div className="flex gap-2">
            <div className="relative flex-1">
              <svg className="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-muted-foreground pointer-events-none" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="11" cy="11" r="8" />
                <path d="m21 21-4.3-4.3" />
              </svg>
              <Input type="number" className="flex-1 pl-8" placeholder="Bangumi ID, e.g. 467461" value={bangumiId} onChange={e => setBangumiId(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') handleSearch(); }} />
            </div>
            <Button onClick={handleSearch} disabled={searching}>{searching ? '查询中...' : '查询'}</Button>
          </div>
          {searchError && <p className="mt-2 text-sm text-destructive">{searchError}</p>}
        </CardContent>
      </Card>

      {/* Results */}
      {result && (
        <Card><CardHeader><CardTitle>📺 字幕组列表 <span className="ml-3 text-sm font-normal text-muted-foreground">Mikan ID: {result.mikan_id}</span></CardTitle></CardHeader>
          <CardContent>
            <div className="flex items-center gap-2 mb-3 p-2 bg-muted rounded-md text-sm"><span className="text-muted-foreground shrink-0">全局 RSS:</span><a href={result.global_rss} target="_blank" rel="noreferrer" className="text-primary break-all">{result.global_rss}</a></div>
            {result.groups.length === 0 ? <p className="text-center py-6 text-muted-foreground text-sm">未找到字幕组</p> : (
              <Table><TableHeader><TableRow><TableHead className="w-8" /><TableHead>字幕组</TableHead><TableHead className="hidden md:table-cell">RSS</TableHead><TableHead>筛选标签</TableHead><TableHead className="w-24" /></TableRow></TableHeader>
                {result.groups.map(g => {
                  const feed = expanded[g.rss_url]; const loading = loadingFeed[g.rss_url] || false; const subMode = getSubMode(g.subgroup_id); const selectedTags = filterTags[g.subgroup_id] || [];
                  const boxOpen = tagBoxOpen[g.subgroup_id] || false;
                  const anyRowExpanded = feed !== undefined || boxOpen;
                  return (<TableBody key={g.subgroup_id}><TableRow className={anyRowExpanded ? 'border-b-0' : ''}>
                    <TableCell><button className="w-7 h-7 flex items-center justify-center rounded border border-border text-muted-foreground hover:text-foreground hover:border-foreground/30 cursor-pointer text-xs bg-transparent" onClick={() => toggleFeed(g.rss_url)} title={feed !== undefined ? '收起' : '展开'}>{loading ? '⏳' : feed !== undefined ? '▲' : '▼'}</button></TableCell>
                    <TableCell className="font-medium">{g.name}</TableCell>
                    <TableCell className="hidden md:table-cell"><a href={g.rss_url} target="_blank" rel="noreferrer" className="text-primary text-xs break-all">{g.rss_url}</a></TableCell>
                    <TableCell>{subMode ? <span className="text-xs text-muted-foreground">—</span> : <button className={`text-xs px-2 py-1 rounded border cursor-pointer transition ${boxOpen ? 'bg-primary/15 border-primary/40 text-primary' : 'border-border text-muted-foreground hover:text-foreground hover:border-foreground/30'}`} onClick={() => toggleTagBox(g.subgroup_id)}>🏷 可选标签{selectedTags.length > 0 && <span className="ml-1 bg-primary/20 text-primary px-1 rounded text-[10px]">{selectedTags.length}</span>}</button>}</TableCell>
                    <TableCell>
                      {subMode ? (
                        <span className={`inline-block px-3 py-1 rounded-full text-xs font-medium ${subMode === 'primary' ? 'bg-primary/15 text-primary' : 'bg-amber-500/15 text-amber-600 dark:text-amber-400'}`}>{subMode === 'primary' ? '主订阅' : '备用订阅'}</span>
                      ) : (
                        <DropdownMenuRoot>
                          <DropdownMenuTrigger className="h-7 text-[0.8rem]">订阅 ▾</DropdownMenuTrigger>
                          <DropdownMenuContent>
                            <DropdownMenuItem onClick={() => doSubscribe(g, 'primary')}>作为主 RSS 订阅</DropdownMenuItem>
                            <DropdownMenuItem onClick={() => doSubscribe(g, 'backup')}>作为备用 RSS 订阅</DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenuRoot>
                      )}
                    </TableCell>
                  </TableRow>
                  {/* Tag selection box */}
                  {boxOpen && !subMode && (<TableRow><TableCell colSpan={5} className="pt-0"><div className="bg-muted/40 rounded-lg p-3 border border-border/50"><div className="text-xs text-muted-foreground mb-2">选择字幕过滤标签（需同时满足）:</div><div className="flex flex-wrap gap-1.5">{AVAILABLE_TAGS.map(tag => { const active = selectedTags.includes(tag); return <button key={tag} className={`text-xs px-2 py-1 rounded border cursor-pointer transition ${active ? TAG_COLORS[tag] + ' border-current' : 'border-border text-muted-foreground hover:text-foreground bg-background'}`} onClick={() => toggleTag(g.subgroup_id, tag)}>{active ? '✓ ' : ''}{tag}</button>; })}</div></div></TableCell></TableRow>)}
                  {/* RSS feed preview */}
                  {feed !== undefined && (<TableRow><TableCell colSpan={5} className="pt-0">{feed === null ? <p className="py-4 text-center text-muted-foreground text-sm">获取失败</p> : feed.items.length === 0 ? <p className="py-4 text-center text-muted-foreground text-sm">该 RSS 暂无条目</p> : (
                    <div className="bg-muted rounded-lg">{feed.items.map((item, i) => { const selected = filterTags[g.subgroup_id] || []; const passed = selected.length === 0 || selected.every(t => item.tags.includes(t)); return (<div key={i} className={`flex flex-wrap items-start gap-2 px-4 py-2.5 text-sm border-b border-border last:border-b-0 ${!passed ? 'opacity-40' : ''}`}><span className={`flex-1 min-w-0 break-all ${item.downloaded || item.excluded ? 'text-muted-foreground' : 'text-foreground'}`}>{item.guid}</span><span className="shrink-0 flex items-center gap-1.5 text-xs">{item.excluded && <span className="text-destructive font-medium">排除</span>}{renderTags(item.tags)}<span className="text-muted-foreground min-w-16 text-right">{formatSize(item.size_bytes)}</span>{passed ? <span className="text-success">✅</span> : <span className="text-destructive">❌</span>}{item.downloaded ? ' ✅' : ''}</span></div>); })}</div>)}</TableCell></TableRow>)}
                  </TableBody>);
                })}
              </Table>)}
          </CardContent>
        </Card>)}

      {/* Subscriptions */}
      <Card><CardHeader><CardTitle>我的订阅 ({subscriptions.length})</CardTitle></CardHeader>
        <CardContent>
          {subLoading ? (
            <p className="text-center py-6 text-muted-foreground">加载中...</p>
          ) : subscriptions.length === 0 ? (
            <p className="text-center py-6 text-muted-foreground text-sm">暂无订阅</p>
          ) : (
            <div className="flex flex-col gap-2">
              {subscriptions.map(s => {
                const totalEps = s.bgm_sortrange ? s.bgm_sortrange[1] - s.bgm_sortrange[0] + 1 : 0;
                const hue = (s.bangumi_id * 137) % 360;
                return (
                  <Card key={s.bangumi_id} size="sm">
                    <CardContent className="flex items-center gap-4 p-3">
                      {/* Cover placeholder */}
                      <div
                        className="shrink-0 w-[92px] h-[130px] rounded-md flex items-center justify-center cursor-pointer text-xs text-white/60"
                        style={{ background: `linear-gradient(135deg, hsl(${hue},50%,40%), hsl(${(hue+40)%360},40%,25%))` }}
                        onClick={() => openHistory(s.bangumi_id)}
                      >
                        <span className="text-2xl font-bold opacity-30">{(s.name || '?')[0]}</span>
                      </div>

                      {/* Info */}
                      <div className="flex-1 min-w-0 self-stretch flex flex-col justify-between">
                        <div>
                          {/* Title */}
                          <p className="text-sm font-semibold truncate cursor-pointer hover:text-primary transition-colors"
                            onClick={() => openHistory(s.bangumi_id)}>{s.name}</p>
                          {/* Metadata row */}
                          <div className="flex flex-wrap items-center gap-1.5 mt-1">
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground">
                              BGM {s.bangumi_id}
                            </span>
                            {s.bgm_season ? (
                              <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground">
                                S{s.bgm_season}
                              </span>
                            ) : null}
                            <span className={`text-[10px] px-1.5 py-0.5 rounded ${s.active !== 0 ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400' : 'bg-muted text-muted-foreground'}`}>
                              {s.active !== 0 ? '启用' : '已完成'}
                            </span>
                            {/* Subgroup */}
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground truncate max-w-[120px]" title={s.subgroup_name}>
                              {s.subgroup_name || '未知字幕组'}
                            </span>
                            {/* Episode progress */}
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-600 dark:text-amber-400">
                              0 / {totalEps || '*'}
                            </span>
                            {s.backup_subgroup_name && (
                              <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground" title={s.backup_subgroup_name}>
                                备用RSS
                              </span>
                            )}
                          </div>
                          {/* Tags */}
                          <div className="flex flex-wrap gap-1 mt-1.5">
                            {s.filter_tags.length > 0 ? renderTags(s.filter_tags) : (
                              <span className="text-[10px] text-muted-foreground">不限</span>
                            )}
                            {s.backup_filter_tags.length > 0 && s.backup_subgroup_name && (
                              <>{renderTags(s.backup_filter_tags.map(t => `备:${t}`))}</>
                            )}
                          </div>
                        </div>
                        {/* Last download */}
                        {s.updated_at && (
                          <p className="text-[10px] text-muted-foreground mt-1">
                            更新于 {s.updated_at.slice(0, 16).replace('T', ' ')}
                          </p>
                        )}
                      </div>

                      {/* Actions — stacked right */}
                      <div className="shrink-0 flex flex-col gap-1.5 self-stretch justify-center">
                        <Button variant="outline" size="sm" className="text-xs h-7 w-16"
                          onClick={() => openHistory(s.bangumi_id)}>历史</Button>
                        {s.active === 0 ? (
                          <Button variant="outline" size="sm" className="text-xs h-7 w-16"
                            onClick={async () => { await rssApi.activateSubscription(s.bangumi_id); await loadSubs(); }}>恢复</Button>
                        ) : (
                          <Button variant="outline" size="sm" className="text-xs h-7 w-16 text-destructive border-destructive/30 hover:bg-destructive/10"
                            onClick={() => unsubscribe(s.bangumi_id)}>取消</Button>
                        )}
                      </div>
                    </CardContent>
                  </Card>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Download history dialog */}
      <DialogRoot open={historyOpen} onOpenChange={(open) => { if (!open) { setHistoryOpen(false); setHistoryBangumiId(0); } }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>📋 {historyData?.name || '…'} · Season {historyData?.bgm_season || '?'}</DialogTitle>
            <DialogClose className="text-muted-foreground hover:text-foreground text-xl w-8 h-8 flex items-center justify-center rounded cursor-pointer">✕</DialogClose>
          </DialogHeader>
          <DialogBody>
            {historyLoading ? (
              <div className="flex flex-col items-center gap-3 py-8"><div className="w-8 h-8 border-4 border-primary/30 border-t-primary rounded-full animate-spin" /><p className="text-sm text-muted-foreground">加载中...</p></div>
            ) : !historyData ? (
              <p className="text-center py-8 text-muted-foreground text-sm">加载失败</p>
            ) : (
              <>
                {/* Stats */}
                <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm mb-4">
                  <span className="text-muted-foreground">已下载 <span className="font-medium text-foreground">{historyData.episodes.length}</span>/{historyData.bgm_sortrange[1] - historyData.bgm_sortrange[0] + 1} 集</span>
                  <span className="text-muted-foreground">主源 <span className="font-medium text-foreground">{historyData.episodes.filter(e => e.source === 'primary').length}</span></span>
                  <span className="text-muted-foreground">备源 <span className="font-medium text-foreground">{historyData.episodes.filter(e => e.source === 'backup').length}</span></span>
                  {historyData.missing_sorts.length > 0 && <span className="text-warning">缺少: EP{historyData.missing_sorts.join(', EP')}</span>}
                  {historyData.missing_sorts.length === 0 && historyData.episodes.length > 0 && <span className="text-success">✅ 已全部下载</span>}
                </div>
                {/* Table */}
                <div className="max-h-80 overflow-y-auto rounded-lg border border-border">
                  <table className="w-full text-sm">
                    <thead className="bg-muted/50 sticky top-0">
                      <tr><th className="px-3 py-2 text-left text-xs font-medium text-muted-foreground w-14">集号</th><th className="px-3 py-2 text-left text-xs font-medium text-muted-foreground w-12">来源</th><th className="px-3 py-2 text-left text-xs font-medium text-muted-foreground w-20">状态</th><th className="px-3 py-2 text-left text-xs font-medium text-muted-foreground">进度</th><th className="px-3 py-2 text-left text-xs font-medium text-muted-foreground">种子名称</th></tr>
                    </thead>
                    <tbody>
                      {historyData.episodes.sort((a, b) => a.sort - b.sort).map(e => {
                        const q = e.qbit;
                        const state = q ? q.state : '未下载';
                        const stateColor = state === 'uploading' || state === 'stalledUP' ? 'text-success' : state === 'downloading' ? 'text-info' : state === 'pausedDL' || state === 'pausedUP' ? 'text-warning' : state === 'queuedDL' || state === 'queuedUP' ? 'text-cyan-600 dark:text-cyan-400' : state === 'missingFiles' ? 'text-destructive' : 'text-muted-foreground';
                        const progress = q ? (q.progress * 100).toFixed(0) + '%' : '—';
                        const stateLabel = state === 'uploading' || state === 'stalledUP' ? '做种中' : state === 'downloading' ? '下载中' : state === 'pausedDL' || state === 'pausedUP' ? '已暂停' : state === 'queuedDL' || state === 'queuedUP' ? '队列中' : state === 'missingFiles' ? '缺文件' : state;
                        return (
                          <tr key={e.sort} className="border-t border-border hover:bg-muted/30">
                            <td className="px-3 py-2 font-medium tabular-nums">EP{e.sort.toString().padStart(2, '0')}</td>
                            <td className="px-3 py-2"><span className={`text-xs px-1.5 py-0.5 rounded-full ${e.source === 'primary' ? 'bg-primary/15 text-primary' : 'bg-amber-500/15 text-amber-600 dark:text-amber-400'}`}>{e.source === 'primary' ? '主' : '备'}</span></td>
                            <td className={`px-3 py-2 text-xs ${stateColor}`}>{stateLabel}</td>
                            <td className="px-3 py-2">
                              {q ? (
                                <div className="flex items-center gap-2">
                                  <div className="w-16 h-1.5 bg-muted rounded-full overflow-hidden"><div className={`h-full rounded-full transition ${q.progress >= 1 ? 'bg-success' : 'bg-primary'}`} style={{ width: `${Math.round(q.progress * 100)}%` }} /></div>
                                  <span className="text-xs text-muted-foreground tabular-nums">{progress}</span>
                                </div>
                              ) : <span className="text-xs text-muted-foreground">—</span>}
                            </td>
                            <td className="px-3 py-2 text-xs text-muted-foreground max-w-48 truncate" title={q?.name || e.guid}>{q?.name || e.guid.slice(0, 60)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </DialogBody>
          <DialogFooter>
            <DialogClose className="inline-flex items-center justify-center rounded-lg border border-border bg-background px-2.5 py-1 text-[0.8rem] font-medium hover:bg-muted cursor-pointer">关闭</DialogClose>
          </DialogFooter>
        </DialogContent>
      </DialogRoot>
    </div>);
}

export default RssManager;
