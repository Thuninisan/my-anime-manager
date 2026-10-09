/**
 * Unified fetch wrapper with timeout, error extraction, and HTML detection.
 */

async function apiFetch<T>(url: string, opts?: RequestInit, timeoutMs = 15_000): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort(opts?.signal?.reason);
  if (opts?.signal?.aborted) abort();
  else opts?.signal?.addEventListener('abort', abort, { once: true });
  const timeout = setTimeout(() => controller.abort(new Error(`请求超时（${timeoutMs / 1000} 秒），请重试`)), timeoutMs);

  const res = await fetch(url, {
    ...opts,
    signal: controller.signal,
    headers: {
      'Accept': 'application/json',
      ...opts?.headers,
    },
  }).finally(() => {
    clearTimeout(timeout);
    opts?.signal?.removeEventListener('abort', abort);
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(body.detail || `HTTP ${res.status}`);
  }

  // Detect HTML responses (e.g. Vite proxy errors, SPA fallback)
  const ct = res.headers.get('content-type') || '';
  if (!ct.includes('application/json')) {
    const text = await res.text().catch(() => '');
    if (text.startsWith('<!DOCTYPE') || text.startsWith('<html')) {
      throw new Error(
        'Backend not reachable — make sure the FastAPI server is running.\n' +
        'Start it with: python -m backend --serve'
      );
    }
    throw new Error(`Expected JSON but got ${ct || 'unknown content-type'}`);
  }

  return res.json();
}

export { apiFetch };
