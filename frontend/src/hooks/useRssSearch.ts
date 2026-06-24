import { useState, useCallback } from 'react';
import type { BangumiMeta, BangumiRssResponse } from '@/types/preview';
import * as rssApi from '@/api/rssApi';

interface UseRssSearchReturn {
  result: BangumiRssResponse | null;
  meta: BangumiMeta | null;
  searching: boolean;
  error: string;
  search: (bangumiId: string) => Promise<void>;
  clear: () => void;
}

export function useRssSearch(): UseRssSearchReturn {
  const [result, setResult] = useState<BangumiRssResponse | null>(null);
  const [meta, setMeta] = useState<BangumiMeta | null>(null);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState('');

  const search = useCallback(async (bangumiId: string) => {
    const id = parseInt(bangumiId.trim(), 10);
    if (!id || id <= 0) { setError('请输入有效的 Bangumi ID'); return; }
    setSearching(true); setError(''); setResult(null); setMeta(null);
    const [rssResult, metaResult] = await Promise.allSettled([
      rssApi.lookupBangumiRss(id),
      rssApi.getBangumiMeta(id),
    ]);
    if (rssResult.status === 'fulfilled') {
      setResult(rssResult.value);
    } else {
      setError(rssResult.reason instanceof Error ? rssResult.reason.message : 'Mikan 搜索失败');
    }
    if (metaResult.status === 'fulfilled') {
      setMeta(metaResult.value);
    }
    setSearching(false);
  }, []);

  const clear = useCallback(() => {
    setResult(null); setMeta(null); setError('');
  }, []);

  return { result, meta, searching, error, search, clear };
}
