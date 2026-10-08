import type { EpisodeMapping } from '@/types/episode';
import { normalizeTorrentPreview } from '@/lib/episodeAdapters';
import type { TorrentPreviewResponse } from '@/types/preview';
import type { CatalogSeason, BangumiCatalogEntry } from '@/types/episode';
import type { AppConfig } from '../types/preview';

const API_BASE = '/api';

export interface TorrentCollection {
  info_hash: string;
  torrent_name: string;
  show_name: string;
  bgm_rating?: number;
  poster_url?: string;
  status: 'downloading' | 'completed' | 'failed';
  created_at: string;
  updated_at: string;
  encoding_group: string;
  video_codec: string;
  bangumi_ids: number[];
  fontinass?: {
    status: string;
    files: { path: string; status: string; attempts: number; code?: number; error?: string }[];
  };
}

export async function retryFontinass(infoHash: string): Promise<void> {
  const res = await fetch(`${API_BASE}/torrent/${encodeURIComponent(infoHash)}/fontinass/retry`, { method: 'POST' });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `重试失败 (HTTP ${res.status})`);
  }
}

export async function getTorrentCollections(): Promise<TorrentCollection[]> {
  const res = await fetch(`${API_BASE}/torrent/collections`);
  if (!res.ok) throw new Error(`Failed to load torrents (HTTP ${res.status})`);
  return res.json();
}

// ── Config ──

export async function getConfig(): Promise<AppConfig> {
  const res = await fetch('/api/settings');
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    // Detect Vite proxy error or SPA fallback HTML
    if (text.startsWith('<!DOCTYPE') || text.startsWith('<html')) {
      throw new Error(
        'Backend not reachable — make sure the FastAPI server is running on port 8000.\n' +
        'Start it with: python -m backend --serve'
      );
    }
    throw new Error(`Failed to fetch config (HTTP ${res.status}): ${text.slice(0, 200)}`);
  }
  // Guard against HTML responses that somehow return 200
  const ct = res.headers.get('content-type') || '';
  if (!ct.includes('application/json')) {
    const text = await res.text().catch(() => '');
    if (text.startsWith('<!DOCTYPE') || text.startsWith('<html')) {
      throw new Error(
        'Backend returned HTML instead of JSON.\n' +
        'If running via --serve: restart the FastAPI server.\n' +
        'If running via npm run dev: make sure the backend is running on port 8000.'
      );
    }
    throw new Error(`Expected JSON but got ${ct || 'unknown content-type'}`);
  }
  return res.json();
}

// ── Parse & Search (primary torrent flow) ──

export async function parseAndSearchTorrent(file: File): Promise<TorrentPreviewResponse> {
  const formData = new FormData();
  formData.append('file', file);

  const res = await fetch(`${API_BASE}/torrent/parse-and-search`, {
    method: 'POST',
    body: formData,
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Parse+Search failed (HTTP ${res.status})`);
  }

  return normalizeTorrentPreview(await res.json());
}

// ── Download (submit to qBittorrent) ──

export interface DownloadFileEntry {
  file_id: string;
  mapping: EpisodeMapping;
  subtitle_suffix?: string;
}

export interface UploadedSubEntry extends DownloadFileEntry {
  stored_filename: string;
  original_filename: string;
}

export interface DownloadRequest {
  preview_id: string;
  preview_revision: number;
  resource_id?: number;
  replace_bangumi_id?: number;
  files: DownloadFileEntry[];
  uploaded_subtitles: UploadedSubEntry[];
}

export interface DownloadResponse {
  ok: boolean;
  info_hash: string;
  message: string;
}

export async function submitDownload(payload: DownloadRequest): Promise<DownloadResponse> {
  const res = await fetch(`${API_BASE}/torrent/download`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Download request failed (HTTP ${res.status})`);
  }

  return res.json();
}

// ── Subtitle upload ──

export interface SubtitleUploadResult {
  ok: boolean;
  filename: string;
  original_filename: string;
  torrent_name: string;
  stored_path: string;
}

export async function uploadSubtitle(
  file: File,
  torrentName: string,
  targetStem: string = '',
): Promise<SubtitleUploadResult> {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('torrent_name', torrentName);
  if (targetStem) {
    formData.append('target_stem', targetStem);
  }

  const res = await fetch(`${API_BASE}/torrent/subtitle/upload`, {
    method: 'POST',
    body: formData,
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Subtitle upload failed (HTTP ${res.status})`);
  }

  return res.json();
}

export async function deleteSubtitle(
  torrentName: string,
  filename: string,
): Promise<{ ok: boolean; deleted: string }> {
  const params = new URLSearchParams({ torrent_name: torrentName, filename });
  const res = await fetch(`${API_BASE}/torrent/subtitle/delete?${params}`, {
    method: 'DELETE',
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Subtitle delete failed (HTTP ${res.status})`);
  }

  return res.json();
}

// ── Episode data lookup by ID ──

export async function fetchTmdbSeasonMap(tmdbId: number): Promise<{ name: string; seasons: Record<string, CatalogSeason> }> {
  const res = await fetch(`${API_BASE}/torrent/catalogs/tmdb/${tmdbId}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function fetchBangumiEpisodes(bangumiId: number): Promise<BangumiCatalogEntry> {
  const res = await fetch(`${API_BASE}/torrent/catalogs/bangumi/${bangumiId}`);
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    if (text.startsWith('<!DOCTYPE') || text.startsWith('<html')) {
      throw new Error('Backend not reachable — make sure the FastAPI server is running on port 8000.');
    }
    throw new Error(`HTTP ${res.status}: ${text.slice(0, 200)}`);
  }
  const ct = res.headers.get('content-type') || '';
  if (!ct.includes('application/json')) {
    const text = await res.text().catch(() => '');
    if (text.startsWith('<!DOCTYPE') || text.startsWith('<html')) {
      throw new Error('Backend returned HTML — restart the FastAPI server to pick up new endpoints.');
    }
    throw new Error(`Expected JSON but got ${ct || 'unknown'}`);
  }
  return res.json();
}

export async function updateConfig(changes: Partial<AppConfig>): Promise<AppConfig> {
  const res = await fetch('/api/settings', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(changes),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Failed to update config (HTTP ${res.status})`);
  }
  return res.json();
}


export async function augmentPreview(preview: TorrentPreviewResponse, provider: 'tmdb' | 'tvdb' | 'bangumi', id: number, showKey?: string): Promise<TorrentPreviewResponse> {
  const keys = Object.keys(preview.search_results);
  const target = showKey ?? (keys.length === 1 ? keys[0] : undefined);
  if (!target || !keys.includes(target)) throw new Error('Select the series to update.');
  const res = await fetch(`${API_BASE}/torrent/previews/${encodeURIComponent(preview.preview_id)}/augment`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ preview_revision: preview.revision, show_key: target,
      provider, ...(provider === 'bangumi' ? { subject_id: id } : { series_id: id }) }),
  });
  if (!res.ok) { const body = await res.json(); throw new Error(body.detail || `HTTP ${res.status}`); }
  return normalizeTorrentPreview(await res.json());
}


export async function setPreviewMatchSource(preview: TorrentPreviewResponse, source: 'tmdb' | 'tvdb'): Promise<TorrentPreviewResponse> {
  const res = await fetch(`${API_BASE}/torrent/previews/${encodeURIComponent(preview.preview_id)}/match-source`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ preview_revision: preview.revision, source }),
  });
  if (!res.ok) { const body = await res.json(); throw new Error(body.detail || `HTTP ${res.status}`); }
  return normalizeTorrentPreview(await res.json());
}
