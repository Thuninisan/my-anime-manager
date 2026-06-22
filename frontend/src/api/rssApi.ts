import type { BangumiRssResponse, RssDataStatus, RssFeedResponse, RssSettings, SubscriptionIn, SubscriptionOut } from '../types/preview';

const API_BASE = '/api/rss';

export async function lookupBangumiRss(bangumiId: number): Promise<BangumiRssResponse> {
  const res = await fetch(`${API_BASE}/bangumi/${bangumiId}`);
  if (!res.ok) {
    const text = await res.text();
    let msg = text;
    try {
      const j = JSON.parse(text);
      msg = j.detail || text;
    } catch { /* not JSON */ }
    throw new Error(msg || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function getDataStatus(): Promise<RssDataStatus> {
  const res = await fetch(`${API_BASE}/data-status`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function downloadData(): Promise<{ ok: boolean; output: string }> {
  const res = await fetch(`${API_BASE}/download-data`, { method: 'POST' });
  if (!res.ok) {
    const text = await res.text();
    let msg = text;
    try {
      const j = JSON.parse(text);
      msg = j.detail || text;
    } catch { /* not JSON */ }
    throw new Error(msg || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function listSubscriptions(): Promise<SubscriptionOut[]> {
  const res = await fetch(`${API_BASE}/subscriptions`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function createSubscription(sub: SubscriptionIn): Promise<SubscriptionOut> {
  const res = await fetch(`${API_BASE}/subscriptions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(sub),
  });
  if (!res.ok) {
    const text = await res.text();
    let msg = text;
    try { const j = JSON.parse(text); msg = j.detail || text; } catch { /* */ }
    throw new Error(msg || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function deleteSubscription(bangumiId: number): Promise<void> {
  const res = await fetch(`${API_BASE}/subscriptions/${bangumiId}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
}

export async function getRssSettings(): Promise<RssSettings> {
  const res = await fetch(`${API_BASE}/settings`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function updateRssSettings(changes: Partial<RssSettings>): Promise<RssSettings> {
  const res = await fetch(`${API_BASE}/settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(changes),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function getDownloaderStatus(): Promise<{ running: boolean; last_run: string; downloaded: number; errors: string[] }> {
  const res = await fetch(`${API_BASE}/downloader/status`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function startDownloader(): Promise<void> {
  await fetch(`${API_BASE}/downloader/start`, { method: 'POST' });
}

export async function stopDownloader(): Promise<void> {
  await fetch(`${API_BASE}/downloader/stop`, { method: 'POST' });
}

export async function runDownloaderOnce(): Promise<void> {
  await fetch(`${API_BASE}/downloader/run-once`, { method: 'POST' });
}

export async function getDownloaderConfig(): Promise<{ poll_interval_min: number; running: boolean }> {
  const res = await fetch(`${API_BASE}/downloader/config`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function setDownloaderInterval(minutes: number): Promise<{ poll_interval_min: number; running: boolean }> {
  const res = await fetch(`${API_BASE}/downloader/config`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ minutes }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function activateSubscription(bangumiId: number): Promise<void> {
  const res = await fetch(`${API_BASE}/subscriptions/${bangumiId}/activate`, { method: 'PATCH' });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
}

export async function checkQbit(): Promise<{ ok: boolean; url: string; version: string; error: string }> {
  const res = await fetch(`${API_BASE}/downloader/qbit-check`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function getDownloadHistory(bangumiId: number): Promise<import('../types/preview').DownloadHistoryResponse> {
  const res = await fetch(`${API_BASE}/subscriptions/${bangumiId}/history`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function fetchRssFeed(
  url: string,
  opts?: { subscriptionId?: string; tags?: string[] },
): Promise<RssFeedResponse> {
  let apiUrl = `${API_BASE}/feed?url=${encodeURIComponent(url)}`;
  if (opts?.subscriptionId) apiUrl += `&subscription_id=${encodeURIComponent(opts.subscriptionId)}`;
  if (opts?.tags?.length) apiUrl += `&tags=${encodeURIComponent(opts.tags.join(','))}`;
  const res = await fetch(apiUrl);
  if (!res.ok) {
    const text = await res.text();
    let msg = text;
    try { const j = JSON.parse(text); msg = j.detail || text; } catch { /* */ }
    throw new Error(msg || `HTTP ${res.status}`);
  }
  return res.json();
}
