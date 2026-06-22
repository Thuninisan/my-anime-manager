import { useState, useEffect, useCallback } from 'react';
import type { SubscriptionIn, SubscriptionOut } from '@/types/preview';
import * as rssApi from '@/api/rssApi';

interface UseSubscriptionsReturn {
  subscriptions: SubscriptionOut[];
  loading: boolean;
  refresh: () => Promise<void>;
  subscribe: (result: { bangumi_id: number; name: string }, group: { name: string; subgroup_id: number; rss_url: string }, role: 'primary' | 'backup', filterTags: Record<number, string[]>) => Promise<void>;
  unsubscribe: (bangumiId: number) => Promise<void>;
  activate: (bangumiId: number) => Promise<void>;
}

export function useSubscriptions(): UseSubscriptionsReturn {
  const [subscriptions, setSubscriptions] = useState<SubscriptionOut[]>([]);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try { setSubscriptions(await rssApi.listSubscriptions()); } catch { /* */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const subscribe = useCallback(async (
    result: { bangumi_id: number; name: string },
    group: { name: string; subgroup_id: number; rss_url: string },
    role: 'primary' | 'backup',
    filterTags: Record<number, string[]>,
  ) => {
    const tags = filterTags[group.subgroup_id] || [];
    const existing = subscriptions.find(s => s.bangumi_id === result.bangumi_id);

    let body: SubscriptionIn;
    if (role === 'primary') {
      body = {
        name: result.name, rss_url: group.rss_url, bangumi_id: result.bangumi_id,
        subgroup_id: group.subgroup_id, subgroup_name: group.name, filter_tags: tags,
        backup_rss_url: existing?.rss_url || '', backup_subgroup_id: existing?.subgroup_id || 0,
        backup_subgroup_name: existing?.subgroup_name || '', backup_filter_tags: existing?.filter_tags || [],
        download_path: existing?.download_path || '',
      };
    } else {
      body = {
        name: result.name, rss_url: existing?.rss_url || '', bangumi_id: result.bangumi_id,
        subgroup_id: existing?.subgroup_id || 0, subgroup_name: existing?.subgroup_name || '',
        filter_tags: existing?.filter_tags || [],
        backup_rss_url: group.rss_url, backup_subgroup_id: group.subgroup_id,
        backup_subgroup_name: group.name, backup_filter_tags: tags,
        download_path: existing?.download_path || '',
      };
    }

    const sub = await rssApi.createSubscription(body);
    setSubscriptions(prev => {
      const idx = prev.findIndex(s => s.bangumi_id === result.bangumi_id);
      if (idx >= 0) { const next = [...prev]; next[idx] = sub; return next; }
      return [...prev, sub];
    });
  }, [subscriptions]);

  const unsubscribe = useCallback(async (bangumiId: number) => {
    await rssApi.deleteSubscription(bangumiId);
    setSubscriptions(prev => prev.filter(s => s.bangumi_id !== bangumiId));
  }, []);

  const activate = useCallback(async (bangumiId: number) => {
    await rssApi.activateSubscription(bangumiId);
    await refresh();
  }, [refresh]);

  return { subscriptions, loading, refresh, subscribe, unsubscribe, activate };
}
