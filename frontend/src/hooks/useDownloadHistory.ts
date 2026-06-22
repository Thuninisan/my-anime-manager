import { useState, useEffect, useCallback } from 'react';
import type { DownloadHistoryResponse } from '@/types/preview';
import * as rssApi from '@/api/rssApi';

interface UseDownloadHistoryReturn {
  open: boolean;
  data: DownloadHistoryResponse | null;
  loading: boolean;
  bangumiId: number;
  openHistory: (id: number) => Promise<void>;
  closeHistory: () => void;
}

export function useDownloadHistory(): UseDownloadHistoryReturn {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<DownloadHistoryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [bangumiId, setBangumiId] = useState(0);

  const fetchHistory = useCallback(async (id: number) => {
    try { setData(await rssApi.getDownloadHistory(id)); } catch { /* */ }
  }, []);

  const openHistory = useCallback(async (id: number) => {
    setLoading(true); setOpen(true); setData(null); setBangumiId(id);
    try { setData(await rssApi.getDownloadHistory(id)); } catch { /* */ }
    finally { setLoading(false); }
  }, []);

  const closeHistory = useCallback(() => {
    setOpen(false); setBangumiId(0);
  }, []);

  // Auto-refresh every 5s while dialog is open
  useEffect(() => {
    if (!open || !bangumiId) return;
    const id = setInterval(() => fetchHistory(bangumiId), 5000);
    return () => clearInterval(id);
  }, [open, bangumiId, fetchHistory]);

  return { open, data, loading, bangumiId, openHistory, closeHistory };
}
