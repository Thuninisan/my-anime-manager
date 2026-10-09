import { useState } from 'react';
import { augmentPreview, removePreviewCandidate } from '@/api/torrentApi';
import { searchBangumi } from '@/api/rssApi';
import type { TorrentPreviewResponse } from '@/types/preview';
import type { EpisodeCatalog } from '@/types/episode';

interface Props {
  searchResult: TorrentPreviewResponse;
  episodeDataOverride?: EpisodeCatalog;
  onEpisodeDataChange: (catalog: EpisodeCatalog) => void;
  onPreviewChange: (view: TorrentPreviewResponse) => void;
}

export default function InfoCards({ searchResult, onPreviewChange }: Props) {
  const keys = Object.keys(searchResult.search_results);
  const [show, setShow] = useState('');
  const target = keys.includes(show) ? show : keys[0];
  const [inputs, setInputs] = useState({ tmdb: '', bangumi: '', tvdb: '' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [results, setResults] = useState<{ bangumi_id: number; name: string }[]>([]);
  const entry = searchResult.search_results[target];
  const catalog = searchResult.episode_catalog;
  const mutate = async (provider: 'tmdb' | 'tvdb' | 'bangumi', id: number, remove = false) => {
    if (!Number.isInteger(id) || id <= 0 || !target) { setError('请输入有效的平台 ID'); return; }
    setBusy(true); setError('');
    try {
      onPreviewChange(await (remove ? removePreviewCandidate(searchResult, provider, id, target)
        : augmentPreview(searchResult, provider, id, target)));
      setInputs(previous => ({ ...previous, [provider]: '' }));
      setResults([]);
    } catch (error) { setError(error instanceof Error ? error.message : String(error)); }
    finally { setBusy(false); }
  };
  return <section className="mb-8 rounded-xl border border-border bg-card p-6">
    <h3 className="font-bold">参与集数匹配的作品候选</h3>
    <p className="mt-2 text-xs text-muted-foreground">添加目录后可逐集调整；点击下载时提交的集数映射决定每个文件使用的作品关系。</p>
    {keys.length > 1 && <select aria-label="作品" className="my-4 rounded border p-2" value={target} disabled={busy} onChange={event => setShow(event.target.value)}>
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
          <input aria-label={`${provider} ID`} className="min-w-0 flex-1 rounded border border-border bg-background p-2 text-sm" placeholder={provider === 'bangumi' ? 'Bangumi ID 或作品名称' : `${provider.toUpperCase()} ID`} value={inputs[provider]} disabled={busy}
            onChange={event => setInputs(previous => ({ ...previous, [provider]: event.target.value }))}
            onKeyDown={event => { if (event.key === 'Enter') void mutate(provider, Number(inputs[provider])); }} />
          <button disabled={busy} className="rounded bg-primary px-3 text-sm text-primary-foreground disabled:opacity-50" onClick={async () => {
            if (provider === 'bangumi' && inputs.bangumi.trim() && !/^\d+$/.test(inputs.bangumi.trim())) {
              setBusy(true); setError('');
              try { setResults(await searchBangumi(inputs.bangumi)); }
              catch (error) { setError(error instanceof Error ? error.message : String(error)); }
              finally { setBusy(false); }
            } else void mutate(provider, Number(inputs[provider]));
          }}>{provider === 'bangumi' && inputs.bangumi && !/^\d+$/.test(inputs.bangumi) ? '搜索' : '添加'}</button>
        </div>
        {provider === 'bangumi' && results.map(result => <button className="block text-left text-xs text-primary" disabled={busy} key={result.bangumi_id} onClick={() => mutate('bangumi', result.bangumi_id)}>{result.name} · {result.bangumi_id}</button>)}
      </div>)}
    </div>
    {error && <p role="alert" className="mt-3 text-sm text-destructive">{error}</p>}
  </section>;
}
