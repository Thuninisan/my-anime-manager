import { normalizeTorrentPreview } from '@/lib/episodeAdapters';
import { apiFetch } from './client';


export interface BangumiResource {
  bangumi_id: number;
  name: string;
  torrents: { resource_id: number; name: string; video_codec: string | string[]; source: string; published_at: string }[];
}

export const listBangumiResources = () => apiFetch<BangumiResource[]>('/api/resources/bangumi');

export interface CollectionStatus {
  running: boolean;
  polling: boolean;
  last_result: { source: string; seen: number; complete: number; failed: number }[];
  errors: string[];
  poll_interval_min: number;
  recognized?: number;
}


export const collectionStatus = () => apiFetch<CollectionStatus>('/api/resources/status');
export const collectNow = () => apiFetch<CollectionStatus>('/api/resources/run-once', { method: 'POST' });
export const setResourcePollInterval = async (minutes: number) => {
  await apiFetch('/api/settings', {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ RESOURCE_POLL_INTERVAL_MIN: minutes }),
  });
  return collectionStatus();
};
// Metadata lookup across several providers can take longer than ordinary API requests.
export const previewResourceTorrent = async (id: number, signal?: AbortSignal) => normalizeTorrentPreview(await apiFetch<unknown>(`/api/resources/${id}/torrent-preview`, { method: 'POST', signal }, 120_000));
export interface ResourceSource { name: string; rss_url: string; downloadtag: { tag: string; attribute: string | null }; index_type: 'tmdb' | 'tvdb' }
export const listResourceSources = () => apiFetch<ResourceSource[]>('/api/resources/sources');
export const addResourceSource = (source: ResourceSource) => apiFetch<ResourceSource>('/api/resources/sources', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(source) });
