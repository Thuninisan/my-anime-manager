import { useState, useCallback, useRef } from 'react';

export interface UpdateStatus {
  update_available: boolean;
  current_sha: string;
  latest_sha: string;
  current_version: string;
  latest_tag: string;
  error?: string;
}

interface UseUpdateCheckReturn {
  status: UpdateStatus | null;
  loading: boolean;
  applying: boolean;
  error: string | null;
  check: () => Promise<void>;
  apply: () => Promise<void>;
}

export function useUpdateCheck(): UseUpdateCheckReturn {
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const checking = useRef(false);

  const check = useCallback(async () => {
    if (checking.current) return;
    checking.current = true;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch('/api/update/check?force=true');
      if (!res.ok) {
        const body = await res.json().catch(() => ({ detail: 'Failed' }));
        throw new Error(body.detail || `HTTP ${res.status}`);
      }
      const data: UpdateStatus = await res.json();
      setStatus(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to check for updates');
    } finally {
      setLoading(false);
      checking.current = false;
    }
  }, []);

  const apply = useCallback(async () => {
    setApplying(true);
    setError(null);
    try {
      const res = await fetch('/api/update/apply', { method: 'POST' });
      if (!res.ok) {
        const body = await res.json().catch(() => ({ detail: 'Failed' }));
        throw new Error(body.detail || `HTTP ${res.status}`);
      }
      // The server will restart after returning this response.
      // We don't try to parse the response body — just wait and poll.
    } catch (err) {
      // Connection errors are expected as the server restarts
      const msg = err instanceof Error ? err.message : '';
      if (msg.includes('Failed to fetch') || msg.includes('NetworkError')) {
        // This is expected — server is restarting
        setApplying(false);
        return;
      }
      setError(err instanceof Error ? err.message : 'Update failed');
      setApplying(false);
    }
  }, []);

  return { status, loading, applying, error, check, apply };
}
