import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
import ts from 'typescript';

const source = readFileSync(new URL('./client.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { apiFetch } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const originalFetch = globalThis.fetch;
const originalSetTimeout = globalThis.setTimeout;
const originalClearTimeout = globalThis.clearTimeout;
let timer;
let cleared;
globalThis.setTimeout = (callback, delay) => { timer = { callback, delay }; return timer; };
globalThis.clearTimeout = (handle) => { cleared = handle; };
globalThis.fetch = (_url, { signal }) => new Promise((resolve, reject) => {
  if (signal.aborted) reject(signal.reason);
  else signal.addEventListener('abort', () => reject(signal.reason), { once: true });
  globalThis.completeRequest = () => resolve(new Response('{"ok":true}', { headers: { 'content-type': 'application/json' } }));
});

try {
  let request = apiFetch('/preview', undefined, 120_000);
  assert.equal(timer.delay, 120_000);
  // A response after the ordinary 15-second deadline still succeeds.
  globalThis.completeRequest();
  assert.deepEqual(await request, { ok: true });
  assert.equal(cleared, timer);

  request = apiFetch('/ordinary');
  assert.equal(timer.delay, 15_000);
  timer.callback();
  await assert.rejects(request, /请求超时/);
  assert.equal(cleared, timer);

  const controller = new AbortController();
  request = apiFetch('/preview', { signal: controller.signal }, 120_000);
  controller.abort();
  await assert.rejects(request, { name: 'AbortError' });
  assert.equal(cleared, timer);

  request = apiFetch('/preview', { signal: new AbortController().signal }, 120_000);
  timer.callback();
  await assert.rejects(request, /请求超时/);

  const cancelled = new AbortController();
  cancelled.abort();
  await assert.rejects(apiFetch('/preview', { signal: cancelled.signal }), { name: 'AbortError' });
  console.log('API request timeout and cancellation: 5 scenarios passed');
} finally {
  globalThis.fetch = originalFetch;
  globalThis.setTimeout = originalSetTimeout;
  globalThis.clearTimeout = originalClearTimeout;
  delete globalThis.completeRequest;
}
