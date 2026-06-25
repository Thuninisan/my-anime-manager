import { useState, useCallback, useRef } from 'react';
import type { DownloadHistoryResponse, SubscriptionOut } from '@/types/preview';
import { getDownloadHistoryStream } from '@/api/rssApi';

interface UseDownloadHistoryReturn {
  open: boolean;
  data: DownloadHistoryResponse | null;
  loading: boolean;
  bangumiId: number;
  subscription: SubscriptionOut | null;
  openHistory: (id: number, sub: SubscriptionOut) => Promise<void>;
  closeHistory: () => void;
  refreshHistory: () => void;
}

export function useDownloadHistory(): UseDownloadHistoryReturn {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<DownloadHistoryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [bangumiId, setBangumiId] = useState(0);
  const [subscription, setSubscription] = useState<SubscriptionOut | null>(null);
  const streamCtrl = useRef<AbortController | null>(null);

  const closeHistory = useCallback(() => {
    // Abort streaming connection
    streamCtrl.current?.abort();
    streamCtrl.current = null;
    setOpen(false);
    setBangumiId(0);
    setSubscription(null);
  }, []);

  const startStream = useCallback((id: number) => {
    streamCtrl.current?.abort();
    setLoading(true);
    streamCtrl.current = getDownloadHistoryStream(
      id,
      (initial) => { setData(initial); setLoading(false); },
      (episodes) => {
        setData((prev) => {
          if (!prev) return prev;
          const updated = prev.episodes.map((e) => {
            const upd = episodes.find((u) => u.sort === e.sort);
            return upd ? { ...e, qbit: upd.qbit } : e;
          });
          return { ...prev, episodes: updated };
        });
      },
      (_err) => { if (!streamCtrl.current) return; },
    );
  }, []);

  const openHistory = useCallback(async (id: number, sub: SubscriptionOut) => {
    setOpen(true);
    setData(null);
    setBangumiId(id);
    setSubscription(sub);
    startStream(id);
  }, [startStream]);

  const refreshHistory = useCallback(() => {
    if (bangumiId) startStream(bangumiId);
  }, [bangumiId, startStream]);

  return { open, data, loading, bangumiId, subscription, openHistory, closeHistory, refreshHistory };
}
