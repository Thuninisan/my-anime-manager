import { useEffect, useState } from 'react';
import type { BangumiMeta, BangumiRssResponse, MikanSearchResult } from '@/types/preview';
import * as rssApi from '@/api/rssApi';
import { showLoadingToast, updateToast } from '@/lib/toast';

interface Props {
  open: boolean;
  bangumiId: number;
  bangumiName: string;
  meta: BangumiMeta | null;
  onClose: () => void;
  onMikanAssigned: (result: BangumiRssResponse) => void;
  onManualSubscribed: () => void;
}

// ── Phase machine ──
type DialogState =
  | { phase: 'searching' }
  | { phase: 'results'; results: MikanSearchResult[] }
  | { phase: 'no-results' }
  | { phase: 'error'; message: string }
  | { phase: 'assigning'; selectedMikanId: number; selectedTitle: string; results: MikanSearchResult[] }
  | { phase: 'assign-error'; message: string; results: MikanSearchResult[] }
  | { phase: 'manual'; results: MikanSearchResult[] | null };

export default function MikanSearchDialog({ open, bangumiId, bangumiName, meta, onClose, onMikanAssigned, onManualSubscribed }: Props) {
  const [state, setState] = useState<DialogState>({ phase: 'searching' });
  // Manual form fields
  const [manualRssUrl, setManualRssUrl] = useState('');
  const [manualBackupUrl, setManualBackupUrl] = useState('');
  const [manualSubscribing, setManualSubscribing] = useState(false);
  const [manualError, setManualError] = useState('');

  // Auto-search Mikan when dialog opens
  useEffect(() => {
    if (!open) return;
    setState({ phase: 'searching' });
    setManualRssUrl('');
    setManualBackupUrl('');
    setManualError('');

    let cancelled = false;
    (async () => {
      try {
        const results = await rssApi.searchMikan(bangumiName);
        if (cancelled) return;
        if (results.length === 0) {
          // No results -> jump to manual mode
          setState({ phase: 'manual', results: null });
        } else {
          setState({ phase: 'results', results });
        }
      } catch (e: unknown) {
        if (cancelled) return;
        setState({ phase: 'error', message: e instanceof Error ? e.message : 'Mikan 搜索失败' });
      }
    })();
    return () => { cancelled = true; };
  }, [open, bangumiName]);

  const handleSelect = async (item: MikanSearchResult) => {
    const results = (state.phase === 'results' ? state.results : []);
    setState({ phase: 'assigning', selectedMikanId: item.mikan_id, selectedTitle: item.title, results });
    try {
      const result = await rssApi.assignMikanId(bangumiId, item.mikan_id);
      onMikanAssigned(result);
    } catch (e: unknown) {
      setState({
        phase: 'assign-error',
        message: e instanceof Error ? e.message : '关联失败',
        results,
      });
    }
  };

  const handleRetrySearch = async () => {
    setState({ phase: 'searching' });
    try {
      const results = await rssApi.searchMikan(bangumiName);
      if (results.length === 0) {
        setState({ phase: 'manual', results: null });
      } else {
        setState({ phase: 'results', results });
      }
    } catch (e: unknown) {
      setState({ phase: 'error', message: e instanceof Error ? e.message : 'Mikan 搜索失败' });
    }
  };

  const handleManualSubscribe = async () => {
    const url = manualRssUrl.trim();
    if (!url) { setManualError('请输入主要 RSS 地址'); return; }
    setManualSubscribing(true);
    setManualError('');
    const toastId = showLoadingToast('订阅中...');
    try {
      await rssApi.manualSubscribe({
        name: bangumiName,
        rss_url: url,
        bangumi_id: bangumiId,
        backup_rss_url: manualBackupUrl.trim(),
      });
      updateToast(toastId, '订阅完成', 'success');
      onManualSubscribed();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : '订阅失败';
      updateToast(toastId, msg, 'error');
      setManualError(msg);
    } finally {
      setManualSubscribing(false);
    }
  };

  const switchToManual = () => {
    const results = state.phase === 'results' ? state.results
      : state.phase === 'assign-error' ? state.results
      : state.phase === 'assigning' ? state.results
      : null;
    setState({ phase: 'manual', results });
    setManualError('');
  };

  if (!open) return null;

  // ── Shared icons ──
  const CloseIcon = (
    <svg className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
      <path d="M6 18L18 6M6 6l12 12" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
  const Spinner = (
    <svg className="animate-spin h-5 w-5" fill="none" viewBox="0 0 24 24">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
    </svg>
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-md p-4">
      <div className="bg-card w-full max-w-lg max-h-[85vh] rounded-xl shadow-2xl overflow-hidden flex flex-col border border-border">
        {/* Header */}
        <header className="relative bg-muted/20 border-b border-border px-6 py-4 shrink-0">
          <button
            className="absolute top-4 right-4 p-1.5 text-muted-foreground hover:text-rose-500 transition-colors cursor-pointer"
            onClick={onClose}
            aria-label="Close"
          >{CloseIcon}</button>

          <h2 className="text-lg font-bold text-foreground pr-8">
            {state.phase === 'manual' ? '手动添加 RSS 订阅' : '关联 Mikan 条目'}
          </h2>
          <p className="text-sm text-muted-foreground mt-1">
            Bangumi: {bangumiName} <span className="text-muted-foreground/60">(ID: {bangumiId})</span>
            {meta?.air_date && <span className="ml-3">开播: {meta.air_date}</span>}
          </p>
        </header>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-6">
          {/* ── Searching ── */}
          {state.phase === 'searching' && (
            <div className="flex flex-col items-center justify-center py-12 gap-4">
              {Spinner}
              <p className="text-sm text-muted-foreground">正在搜索 Mikan...</p>
            </div>
          )}

          {/* ── Error ── */}
          {state.phase === 'error' && (
            <div className="flex flex-col items-center gap-4 py-8">
              <div className="p-3 rounded-full bg-destructive/10">
                <svg className="h-6 w-6 text-destructive" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
                  <circle cx="12" cy="12" r="10" /><line x1="12" y1="8" x2="12" y2="12" /><line x1="12" y1="16" x2="12.01" y2="16" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </div>
              <p className="text-sm text-destructive text-center">{state.message}</p>
              <div className="flex gap-3">
                <button
                  className="px-4 py-2 bg-primary text-primary-foreground rounded-lg text-xs font-semibold cursor-pointer hover:brightness-110"
                  onClick={handleRetrySearch}
                >重试</button>
                <button
                  className="px-4 py-2 bg-muted text-muted-foreground rounded-lg text-xs font-semibold cursor-pointer hover:bg-muted/70"
                  onClick={switchToManual}
                >手动输入 RSS</button>
              </div>
            </div>
          )}

          {/* ── Results ── */}
          {(state.phase === 'results' || state.phase === 'assigning' || state.phase === 'assign-error') && (
            <div className="space-y-3">
              {state.phase === 'assign-error' && (
                <p className="text-sm text-destructive bg-destructive/10 rounded-lg px-3 py-2">{state.message}</p>
              )}
              {state.phase === 'assign-error' && (
                <div className="flex gap-3">
                  <button
                    className="px-4 py-2 bg-primary text-primary-foreground rounded-lg text-xs font-semibold cursor-pointer hover:brightness-110"
                    onClick={handleRetrySearch}
                  >重新搜索</button>
                </div>
              )}

              <p className="text-xs text-muted-foreground">
                Mikan 搜索结果 <span className="font-medium text-foreground">({state.phase === 'results' ? state.results.length : (state as { results: MikanSearchResult[] }).results.length} 条)</span>
              </p>

              <div className="space-y-1.5">
                {(state.phase === 'results' ? state.results : (state as { results: MikanSearchResult[] }).results).map(item => {
                  const isAssigning = state.phase === 'assigning' && state.selectedMikanId === item.mikan_id;
                  return (
                    <div
                      key={item.mikan_id}
                      className="flex items-center justify-between p-3 border border-border rounded-lg hover:bg-muted/50 transition-colors"
                    >
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium truncate">{item.title}</p>
                        <p className="text-[11px] text-muted-foreground">Mikan ID: {item.mikan_id}</p>
                      </div>
                      {isAssigning ? (
                        <span className="ml-3 shrink-0 text-primary">{Spinner}</span>
                      ) : (
                        <button
                          className="ml-3 shrink-0 px-3 py-1.5 bg-primary text-primary-foreground rounded-md text-xs font-semibold cursor-pointer hover:brightness-110 transition-all disabled:opacity-40"
                          disabled={state.phase === 'assigning'}
                          onClick={() => handleSelect(item)}
                        >选择</button>
                      )}
                    </div>
                  );
                })}
              </div>

              <div className="pt-3 border-t border-border">
                <button
                  className="text-xs text-muted-foreground hover:text-primary underline cursor-pointer transition-colors"
                  onClick={switchToManual}
                >手动输入 RSS 地址</button>
              </div>
            </div>
          )}

          {/* ── Manual RSS entry ── */}
          {state.phase === 'manual' && (
            <div className="space-y-4">
              {state.results === null && (
                <div className="p-3 rounded-lg bg-amber-500/10 border border-amber-500/20">
                  <p className="text-xs text-amber-600">
                    Mikan 未找到 "{bangumiName}" 的匹配结果。请手动输入 RSS 地址。
                  </p>
                </div>
              )}

              {state.results !== null && state.results.length > 0 && (
                <button
                  className="text-xs text-muted-foreground hover:text-primary underline cursor-pointer transition-colors"
                  onClick={() => setState({ phase: 'results', results: state.results! })}
                >返回搜索结果</button>
              )}

              <div className="space-y-3">
                <div>
                  <label className="text-xs font-medium text-foreground mb-1 block">主要 RSS 地址 *</label>
                  <input
                    type="text"
                    className="w-full px-3 py-2 text-sm border border-border rounded-lg bg-background text-foreground placeholder:text-muted-foreground outline-none focus:border-primary"
                    placeholder="https://mikanani.me/RSS/Bangumi?bangumiId=..."
                    value={manualRssUrl}
                    onChange={e => setManualRssUrl(e.target.value)}
                  />
                </div>
                <div>
                  <label className="text-xs font-medium text-foreground mb-1 block">备用 RSS 地址 (可选)</label>
                  <input
                    type="text"
                    className="w-full px-3 py-2 text-sm border border-border rounded-lg bg-background text-foreground placeholder:text-muted-foreground outline-none focus:border-primary"
                    placeholder="https://mikanani.me/RSS/Bangumi?..."
                    value={manualBackupUrl}
                    onChange={e => setManualBackupUrl(e.target.value)}
                  />
                </div>
              </div>

              {manualError && (
                <p className="text-sm text-destructive">{manualError}</p>
              )}

              <button
                className="w-full py-2.5 bg-primary text-primary-foreground rounded-lg text-sm font-semibold cursor-pointer hover:brightness-110 transition-all disabled:opacity-50 flex items-center justify-center gap-2"
                disabled={manualSubscribing}
                onClick={handleManualSubscribe}
              >
                {manualSubscribing && Spinner}
                {manualSubscribing ? '订阅中...' : '订阅'}
              </button>
            </div>
          )}
        </div>

        {/* Footer */}
        <footer className="px-5 py-3 border-t border-border bg-muted/20 flex justify-between items-center shrink-0">
          <span className="text-[10px] uppercase tracking-wider text-muted-foreground font-semibold">
            Mikan Search
          </span>
          <button
            className="bg-muted text-foreground px-5 py-2 rounded-lg text-xs font-semibold hover:bg-muted/70 active:scale-95 transition-all cursor-pointer"
            onClick={onClose}
          >取消</button>
        </footer>
      </div>
    </div>
  );
}
