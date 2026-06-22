import { useState, useCallback } from 'react';
import type { BangumiRssResponse } from '@/types/preview';
import * as rssApi from '@/api/rssApi';

interface UseRssSearchReturn {
  result: BangumiRssResponse | null;
  searching: boolean;
  error: string;
  search: (bangumiId: string) => Promise<void>;
  clear: () => void;
}

export function useRssSearch(): UseRssSearchReturn {
  const [result, setResult] = useState<BangumiRssResponse | null>(null);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState('');

  const search = useCallback(async (bangumiId: string) => {
    const id = parseInt(bangumiId.trim(), 10);
    if (!id || id <= 0) { setError('请输入有效的 Bangumi ID'); return; }
    setSearching(true); setError(''); setResult(null);
    try {
      setResult(await rssApi.lookupBangumiRss(id));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Search failed');
    } finally {
      setSearching(false);
    }
  }, []);

  const clear = useCallback(() => {
    setResult(null); setError('');
  }, []);

  return { result, searching, error, search, clear };
}
