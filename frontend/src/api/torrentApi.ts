import type { AppConfig } from '../types/preview';

const API_BASE = '/api';

// ── Config ──

export async function getConfig(): Promise<AppConfig> {
  const res = await fetch('/config');
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    // Detect Vite proxy error or SPA fallback HTML
    if (text.startsWith('<!DOCTYPE') || text.startsWith('<html')) {
      throw new Error(
        'Backend not reachable — make sure the FastAPI server is running on port 8000.\n' +
        'Start it with: python -m my_anime_manager --serve'
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

export async function parseAndSearchTorrent(file: File): Promise<any> {
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

export async function fetchTmdbSeasonMap(tmdbId: number): Promise<Record<string, { name: string; episodes: { epNum: number; tmdbId: number; name: string }[] }>> {
  const res = await fetch(`/api/rss/tmdb/${tmdbId}/seasons`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function fetchBangumiEpisodes(bangumiId: number): Promise<{ id: number; name: string; episodes: { sort: number; id: number; name: string; name_cn?: string }[] }> {
  const res = await fetch(`${API_BASE}/torrent/bangumi/${bangumiId}/episodes`);
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
  const res = await fetch('/config', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(changes),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Failed to update config (HTTP ${res.status})`);
  }
  return res.json();
}
