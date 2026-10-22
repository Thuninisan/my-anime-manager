import { useEffect, useRef, useState } from 'react';
import { augmentPreview, removePreviewCandidate } from '@/api/torrentApi';
import { searchBangumiOnline, type BangumiOnlineResult } from '@/api/rssApi';
import SearchResultDialog from '@/components/shared/SearchResultDialog';
import type { TorrentPreviewResponse } from '@/types/preview';

interface Props {
  searchResult: TorrentPreviewResponse;
  onPreviewChange: (view: TorrentPreviewResponse) => void;
}

interface OnlineSearch {
  request: number;
  previewId: string;
  target: string;
  query: string;
  results: BangumiOnlineResult[];
  loading: boolean;
  error: string;
}

export default function InfoCards({ searchResult, onPreviewChange }: Props) {
  const keys = Object.keys(searchResult.search_results);
  const [show, setShow] = useState('');
  const target = keys.includes(show) ? show : keys[0];
  const [inputs, setInputs] = useState({ tmdb: '', bangumi: '', tvdb: '' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [onlineSearch, setOnlineSearch] = useState<OnlineSearch | null>(null);
  const requestRef = useRef(0);
  const mutatingRef = useRef(false);
  const composingRef = useRef(false);
  const activeSearch = onlineSearch?.previewId === searchResult.preview_id && onlineSearch.target === target ? onlineSearch : null;
  useEffect(() => () => { ++requestRef.current; }, []);
  const closeSearch = () => {
    ++requestRef.current;
    setOnlineSearch(null);
  };
  const entry = searchResult.search_results[target];
  const catalog = searchResult.episode_catalog;
  const mutate = async (provider: 'tmdb' | 'tvdb' | 'bangumi', id: number, remove = false, selection = false) => {
    if (mutatingRef.current) return;
    if (!Number.isInteger(id) || id <= 0 || !target) { setError('请输入有效的平台 ID'); return; }
    mutatingRef.current = true;
    setBusy(true); setError('');
    if (selection) setOnlineSearch(current => current ? { ...current, error: '' } : current);
    try {
      onPreviewChange(await (remove ? removePreviewCandidate(searchResult, provider, id, target)
        : augmentPreview(searchResult, provider, id, target)));
      setInputs(previous => ({ ...previous, [provider]: '' }));
      closeSearch();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      if (selection) setOnlineSearch(current => current ? { ...current, error: message } : current);
      else setError(message);
    }
    finally { mutatingRef.current = false; setBusy(false); }
  };
  const submit = async (provider: 'tmdb' | 'tvdb' | 'bangumi') => {
    if (mutatingRef.current) return;
    const query = inputs[provider].trim();
    if (!query) { setError(provider === 'bangumi' ? '请输入 Bangumi ID 或作品名称' : '请输入有效的平台 ID'); return; }
    if (provider !== 'bangumi' || /^\d+$/.test(query)) {
      if (!/^\d+$/.test(query)) { setError('请输入有效的平台 ID'); return; }
      await mutate(provider, Number(query));
      return;
    }
    if (!target) { setError('请先选择要添加候选的作品'); return; }
    const request = ++requestRef.current;
    setError('');
    setOnlineSearch({ request, previewId: searchResult.preview_id, target, query, results: [], loading: true, error: '' });
    try {
      const results = await searchBangumiOnline(query);
      // 关闭弹窗或重新搜索后，忽略旧请求的结果。
      if (request !== requestRef.current) return;
      setOnlineSearch(current => current?.request === request ? { ...current, results, loading: false } : current);
    } catch (error) {
      if (request !== requestRef.current) return;
      setOnlineSearch(current => current?.request === request ? {
        ...current, loading: false, error: error instanceof Error ? error.message : 'Bangumi 搜索失败',
      } : current);
    }
  };
  return <section className="mb-8 rounded-xl border border-border bg-card p-6">
    <h3 className="font-bold">参与集数匹配的作品候选</h3>
    <p className="mt-2 text-xs text-muted-foreground">添加目录后可逐集调整；点击下载时提交的集数映射决定每个文件使用的作品关系。</p>
    {keys.length > 1 && <select aria-label="作品" className="my-4 rounded border p-2" value={target} disabled={busy} onChange={event => { closeSearch(); setShow(event.target.value); }}>
      {keys.map(key => <option key={key} value={key}>{key}</option>)}
    </select>}
    <div className="mt-4 grid gap-6 md:grid-cols-3">
      {(['tmdb', 'bangumi', 'tvdb'] as const).filter(provider => entry?.media_type !== 'movie' || provider !== 'tvdb').map(provider => <div key={provider} className="space-y-3">
        <h4 className="font-semibold">{provider.toUpperCase()} Candidates</h4>
        {entry?.candidates[provider].map((candidate, index) => {
          const loaded = !!catalog[provider]?.[String(candidate.provider_id)] || provider === 'tmdb' && entry.media_type === 'movie';
          return <div key={candidate.provider_id} className="rounded border border-border p-2 text-xs">
            <p>{candidate.title || candidate.original_title || provider.toUpperCase()} · ID {candidate.provider_id}</p>
            <p className="mt-1 text-muted-foreground">{loaded ? '目录可用' : '待加载'}{index === 0 ? ' · 默认推荐' : ''} · {candidate.source}</p>
            <div className="mt-2 flex gap-3">
              <button disabled={busy} className="text-primary disabled:opacity-50" onClick={() => mutate(provider, candidate.provider_id)}>{entry.media_type === 'movie' && provider === 'tmdb' ? '用于电影匹配' : '加载目录'}</button>
              <button disabled={busy} className="text-destructive disabled:opacity-50" onClick={() => mutate(provider, candidate.provider_id, true)}>移除</button>
            </div>
          </div>;
        })}
        <div className="flex gap-2">
          <input aria-label={provider === 'bangumi' ? 'Bangumi ID 或作品名称' : `${provider.toUpperCase()} ID`} className="min-w-0 flex-1 rounded border border-border bg-background p-2 text-sm" placeholder={provider === 'bangumi' ? 'Bangumi ID 或作品名称' : `${provider.toUpperCase()} ID`} value={inputs[provider]} disabled={busy}
            onChange={event => setInputs(previous => ({ ...previous, [provider]: event.target.value }))}
            onCompositionStart={() => { composingRef.current = true; }}
            onCompositionEnd={() => { composingRef.current = false; }}
            onKeyDown={event => {
              if (event.key === 'Enter' && !composingRef.current && !event.nativeEvent.isComposing && event.nativeEvent.keyCode !== 229) {
                event.preventDefault();
                void submit(provider);
              }
            }} />
          <button type="button" disabled={busy} className="rounded bg-primary px-3 text-sm text-primary-foreground disabled:opacity-50" onClick={() => { void submit(provider); }}>
            {provider === 'bangumi' && inputs.bangumi.trim() && !/^\d+$/.test(inputs.bangumi.trim()) ? '搜索' : '添加'}
          </button>
        </div>
      </div>)}
    </div>
    {error && <p role="alert" className="mt-3 text-sm text-destructive">{error}</p>}
    {activeSearch && <SearchResultDialog {...activeSearch} selecting={busy} onClose={closeSearch}
      onSelect={item => { void mutate('bangumi', item.bangumi_id, false, true); }} />}
  </section>;
}
