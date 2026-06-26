import type { BangumiRssResponse, RssDataStatus, RssFeedResponse, RssSettings, SeasonInfo, SubscriptionIn, SubscriptionOut } from '../types/preview';

const API_BASE = '/api/rss';

export async function searchBangumi(query: string): Promise<{ bangumi_id: number; name: string }[]> {
  const res = await fetch(`${API_BASE}/search?q=${encodeURIComponent(query)}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function getBangumiMeta(bangumiId: number): Promise<import('@/types/preview').BangumiMeta> {
  const res = await fetch(`${API_BASE}/bangumi/${bangumiId}/meta`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

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

/** Subscribe and stream enrichment progress via NDJSON.
 *  Returns the enriched SubscriptionOut on success. */
export async function createSubscriptionWithProgress(
  sub: SubscriptionIn,
  onProgress: (msg: string) => void,
): Promise<SubscriptionOut> {
  // 1. Create subscription (fast, no enrichment)
  const subRes = await createSubscription(sub);

  // 2. Stream enrichment progress via NDJSON
  const enrichRes = await fetch(
    `${API_BASE}/subscriptions/${sub.bangumi_id}/enrich-stream`,
    { method: "POST" },
  );
  if (!enrichRes.ok) throw new Error(`Enrich stream HTTP ${enrichRes.status}`);

  const reader = enrichRes.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop()!; // keep incomplete last line
    for (const line of lines) {
      if (!line.trim()) continue;
      const evt = JSON.parse(line);
      if (evt.type === "step") onProgress(evt.message);
      if (evt.type === "error") throw new Error(evt.message);
      if (evt.type === "done") {
        if (evt.result) Object.assign(subRes, evt.result);
        return subRes;
      }
    }
  }
  throw new Error("Enrich stream ended without done event");
}

export async function deleteSubscription(bangumiId: number, deleteFiles?: boolean): Promise<void> {
  const url = deleteFiles
    ? `${API_BASE}/subscriptions/${bangumiId}?delete_files=true`
    : `${API_BASE}/subscriptions/${bangumiId}`;
  const res = await fetch(url, { method: 'DELETE' });
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

export async function updateSubscription(bangumiId: number, fields: Record<string, unknown>): Promise<void> {
  const res = await fetch(`${API_BASE}/subscriptions/${bangumiId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(fields),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
}

export async function deleteSubscriptionRss(bangumiId: number, type: 'primary' | 'backup'): Promise<{ deleted: boolean }> {
  const res = await fetch(`${API_BASE}/subscriptions/${bangumiId}/rss?type=${type}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function checkQbit(): Promise<{ ok: boolean; url: string; version: string; error: string }> {
  const res = await fetch(`${API_BASE}/downloader/qbit-check`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function deleteEpisodeHistory(bangumiId: number, sort: number): Promise<void> {
  const res = await fetch(`${API_BASE}/download-history/${bangumiId}/${sort}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
}

export async function updateEpisodeHistory(bangumiId: number, sort: number, fields: Record<string, unknown>): Promise<void> {
  const res = await fetch(`${API_BASE}/download-history/${bangumiId}/${sort}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(fields),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
}

export async function addEpisodeHistory(bangumiId: number, sort: number): Promise<void> {
  const res = await fetch(`${API_BASE}/download-history/${bangumiId}/${sort}`, { method: 'POST' });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
}

export async function addEpisodeWithTorrent(
  bangumiId: number,
  sort: number,
  file: File,
): Promise<{ torrent_name: string; info_hash: string }> {
  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch(`${API_BASE}/download-history/${bangumiId}/${sort}/upload`, {
    method: 'POST',
    body: fd,
  });
  if (!res.ok) {
    const text = await res.text();
    let msg = text;
    try { const j = JSON.parse(text); msg = j.detail || text; } catch { /* */ }
    throw new Error(msg || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function replaceEpisodeWithTorrent(
  bangumiId: number,
  sort: number,
  file: File,
): Promise<{ torrent_name: string; info_hash: string }> {
  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch(`${API_BASE}/download-history/${bangumiId}/${sort}/replace`, {
    method: 'POST',
    body: fd,
  });
  if (!res.ok) {
    const text = await res.text();
    let msg = text;
    try { const j = JSON.parse(text); msg = j.detail || text; } catch { /* */ }
    throw new Error(msg || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function getDownloadHistory(bangumiId: number): Promise<import('../types/preview').DownloadHistoryResponse> {
  const res = await fetch(`${API_BASE}/subscriptions/${bangumiId}/history`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

/** Stream download history with live qBittorrent updates via NDJSON.
 *  Returns an AbortController — call .abort() to stop the stream. */
export function getDownloadHistoryStream(
  bangumiId: number,
  onData: (data: import('../types/preview').DownloadHistoryResponse) => void,
  onUpdate: (episodes: { sort: number; qbit: import('../types/preview').QbitTorrentInfo | null }[]) => void,
  onError: (err: Error) => void,
): AbortController {
  const ctrl = new AbortController();

  (async () => {
    try {
      const res = await fetch(`${API_BASE}/subscriptions/${bangumiId}/history-stream`, {
        signal: ctrl.signal,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop()!;
        for (const line of lines) {
          if (!line.trim()) continue;
          const evt = JSON.parse(line);
          if (evt.type === 'data') onData(evt);
          else if (evt.type === 'update') onUpdate(evt.episodes);
        }
      }
    } catch (err: any) {
      if (err.name !== 'AbortError') onError(err);
    }
  })();

  return ctrl;
}

export async function getTmdbSeasonMap(tmdbId: number): Promise<Record<string, SeasonInfo>> {
  const res = await fetch(`/api/rss/tmdb/${tmdbId}/seasons`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function fetchRssFeed(
  url: string,
  opts?: { subscriptionId?: string; tags?: string[]; excludePatterns?: string[] },
): Promise<RssFeedResponse> {
  let apiUrl = `${API_BASE}/feed?url=${encodeURIComponent(url)}`;
  if (opts?.subscriptionId) apiUrl += `&subscription_id=${encodeURIComponent(opts.subscriptionId)}`;
  if (opts?.tags?.length) apiUrl += `&tags=${encodeURIComponent(opts.tags.join(','))}`;
  if (opts?.excludePatterns?.length) apiUrl += `&exclude_patterns=${encodeURIComponent(opts.excludePatterns.join(','))}`;
  const res = await fetch(apiUrl);
  if (!res.ok) {
    const text = await res.text();
    let msg = text;
    try { const j = JSON.parse(text); msg = j.detail || text; } catch { /* */ }
    throw new Error(msg || `HTTP ${res.status}`);
  }
  return res.json();
}
