import { useState, useCallback, useRef } from 'react';
import type { BangumiMeta, BangumiRssResponse } from '@/types/preview';
import * as rssApi from '@/api/rssApi';

interface UseRssSearchReturn {
  result: BangumiRssResponse | null;
  meta: BangumiMeta | null;
  searching: boolean;
  error: string;
  mikanFallback: { bangumi_id: number; name: string } | null;
  search: (bangumiId: string, candidate?: { has_mikan_id: boolean; name: string }) => Promise<void>;
  clear: () => void;
  setExternalResult: (result: BangumiRssResponse, meta: BangumiMeta | null) => void;
}

export function useRssSearch(): UseRssSearchReturn {
  const [result, setResult] = useState<BangumiRssResponse | null>(null);
  const [meta, setMeta] = useState<BangumiMeta | null>(null);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState('');
  const [mikanFallback, setMikanFallback] = useState<{ bangumi_id: number; name: string } | null>(null);
  const requestRef = useRef(0);

  const search = useCallback(async (bangumiId: string, candidate?: { has_mikan_id: boolean; name: string }) => {
    const id = parseInt(bangumiId.trim(), 10);
    if (!id || id <= 0) { setError('请输入有效的 Bangumi ID'); return; }
    const request = ++requestRef.current;
    setSearching(true); setError(''); setResult(null); setMeta(null); setMikanFallback(null);
    const [rssResult, metaResult] = await Promise.allSettled([
      candidate && !candidate.has_mikan_id
        ? Promise.resolve(null)
        : rssApi.lookupBangumiRss(id),
      rssApi.getBangumiMeta(id),
    ]);
    if (request !== requestRef.current) return;
    const bangumiMeta = metaResult.status === 'fulfilled' ? metaResult.value : null;
    if (rssResult.status === 'fulfilled' && rssResult.value) {
      setResult(rssResult.value);
    } else if (rssResult.status === 'fulfilled' || (rssResult.reason instanceof rssApi.MikanMappingNotFoundError)) {
      setMikanFallback({ bangumi_id: id, name: candidate?.name || bangumiMeta?.series_name || String(id) });
    } else {
      setError(rssResult.reason instanceof Error ? rssResult.reason.message : 'Mikan 搜索失败');
    }
    if (metaResult.status === 'fulfilled') {
      setMeta(metaResult.value);
    }
    setSearching(false);
  }, []);

  const clear = useCallback(() => {
    ++requestRef.current;
    setResult(null); setMeta(null); setError(''); setMikanFallback(null); setSearching(false);
  }, []);

  // Set result/meta from external source (MikanSearchDialog flow)
  const setExternalResult = useCallback((rssResult: BangumiRssResponse, bangumiMeta: BangumiMeta | null) => {
    ++requestRef.current;
    setMikanFallback(null);
    setResult(rssResult);
    setMeta(bangumiMeta);
    setError('');
    setSearching(false);
  }, []);

  return { result, meta, searching, error, mikanFallback, search, clear, setExternalResult };
}
