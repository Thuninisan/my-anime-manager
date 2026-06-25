import type { DownloadHistoryResponse, SubscriptionOut } from '@/types/preview';

interface Props {
  open: boolean;
  data: DownloadHistoryResponse | null;
  loading: boolean;
  subscription: SubscriptionOut | null;
  onClose: () => void;
}

/* ── Status helpers ─────────────────────────────────────────────── */

function statusInfo(state: string | undefined) {
  if (!state) return { label: 'Unknown', color: 'bg-muted-foreground', textColor: 'text-muted-foreground' };
  const map: Record<string, { label: string; color: string; textColor: string }> = {
    uploading:     { label: 'Seeding',       color: 'bg-emerald-500',  textColor: 'text-emerald-600' },
    stalledUP:     { label: 'Seeding',       color: 'bg-emerald-500',  textColor: 'text-emerald-600' },
    downloading:   { label: 'Downloading',   color: 'bg-sky-400',      textColor: 'text-sky-600' },
    pausedDL:      { label: 'Paused',        color: 'bg-amber-400',    textColor: 'text-amber-600' },
    pausedUP:      { label: 'Paused',        color: 'bg-amber-400',    textColor: 'text-amber-600' },
    queuedDL:      { label: 'Queued',        color: 'bg-amber-400',    textColor: 'text-amber-500' },
    queuedUP:      { label: 'Queued',        color: 'bg-amber-400',    textColor: 'text-amber-500' },
    missingFiles:  { label: 'Missing Files', color: 'bg-red-500',      textColor: 'text-red-600' },
  };
  return map[state] || { label: state, color: 'bg-muted-foreground', textColor: 'text-muted-foreground' };
}

function formatBytes(bytes: number | undefined): string {
  if (!bytes || bytes <= 0) return '';
  if (bytes >= 1073741824) return (bytes / 1073741824).toFixed(1) + ' GB';
  if (bytes >= 1048576) return (bytes / 1048576).toFixed(1) + ' MB';
  if (bytes >= 1024) return (bytes / 1024).toFixed(0) + ' KB';
  return bytes + ' B';
}

function formatSpeed(bytesPerSec: number | undefined): string {
  if (!bytesPerSec || bytesPerSec <= 0) return '';
  return formatBytes(bytesPerSec) + '/s';
}

/* ── Component ──────────────────────────────────────────────────── */

export default function DownloadHistoryDialog({ open, data, loading, subscription, onClose }: Props) {
  if (!open) return null;

  const totalEps = data
    ? data.bgm_sortrange[1] - data.bgm_sortrange[0] + 1
    : 0;
  const downloaded = data ? data.episodes.length : 0;
  const missing = data?.missing_sorts || [];
  const isActive = subscription?.active !== 0;
  const sortedEps = data ? [...data.episodes].sort((a, b) => a.sort - b.sort) : [];
  const totalSize = sortedEps.reduce((sum, e) => sum + (e.qbit?.size || 0), 0);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-sm">
      <div className="bg-card w-full max-w-5xl h-[85vh] rounded-xl shadow-2xl overflow-hidden flex flex-col md:flex-row">
        {/* ═══════════════════ Left Sidebar ═══════════════════ */}
        <aside className="w-full md:w-72 bg-muted/40 border-r border-border flex flex-col shrink-0">
          {/* Poster */}
          <div className="p-5">
            <div className="relative aspect-[3/4] rounded-lg overflow-hidden shadow-sm">
              {subscription?.poster_url ? (
                <img
                  src={subscription.poster_url}
                  alt={subscription.name}
                  className="w-full h-full object-cover"
                />
              ) : (
                <div
                  className="w-full h-full"
                  style={{
                    background: `linear-gradient(135deg, hsl(${(subscription?.bangumi_id || 1) * 137 % 360},45%,35%), hsl(${((subscription?.bangumi_id || 1) * 137 + 40) % 360},35%,20%))`,
                  }}
                >
                  <div className="absolute inset-0 flex items-center justify-center">
                    <span className="text-4xl font-bold text-white/25">
                      {(subscription?.name || data?.name || '?')[0]}
                    </span>
                  </div>
                </div>
              )}
              {(subscription?.bgm_rating != null && subscription.bgm_rating > 0) && (
                <div className="absolute top-3 left-3 bg-secondary text-white text-[10px] font-bold px-2 py-1 rounded-full shadow-sm">
                  BGM {subscription.bgm_rating.toFixed(1)} / 10
                </div>
              )}
            </div>
          </div>

          {/* Metadata */}
          <div className="px-5 pb-5 flex-1">
            <h2 className="text-lg font-bold text-foreground leading-tight">
              {subscription?.name || data?.name || '...'}
            </h2>
            <p className="text-sm text-muted-foreground mt-1">
              Season {data?.bgm_season || subscription?.bgm_season || '?'}
            </p>

            <div className="mt-5 space-y-3">
              <div>
                <span className="text-[10px] uppercase tracking-wider text-muted-foreground block">Status</span>
                <span className={`text-sm font-semibold ${isActive ? 'text-accent' : 'text-muted-foreground'}`}>
                  {isActive ? 'Ongoing' : 'Completed'}
                </span>
              </div>
              {subscription?.subgroup_name && (
                <div>
                  <span className="text-[10px] uppercase tracking-wider text-muted-foreground block">Sub Group</span>
                  <span className="text-sm font-semibold text-foreground">{subscription.subgroup_name}</span>
                </div>
              )}
              {subscription?.filter_tags && subscription.filter_tags.length > 0 && (
                <div>
                  <span className="text-[10px] uppercase tracking-wider text-muted-foreground block">Filter</span>
                  <span className="text-sm font-semibold text-foreground">{subscription.filter_tags.join(', ')}</span>
                </div>
              )}
              {subscription?.bgm_rating != null && subscription.bgm_rating > 0 && (
                <div>
                  <span className="text-[10px] uppercase tracking-wider text-muted-foreground block">Rating</span>
                  <span className="text-sm font-semibold text-foreground">
                    {subscription.bgm_rating.toFixed(1)}
                    <span className="text-muted-foreground font-normal"> / 10</span>
                    <span className="text-muted-foreground font-normal text-xs ml-1">
                      ({subscription.bgm_rating_total?.toLocaleString() ?? 0} votes)
                    </span>
                  </span>
                </div>
              )}
            </div>
          </div>

          {/* Sidebar actions */}
          <div className="p-5 border-t border-border space-y-2">
            <button className="w-full py-2 px-4 border border-border text-muted-foreground rounded-lg text-xs font-medium hover:bg-muted transition-colors">
              Edit Subscription
            </button>
          </div>
        </aside>

        {/* ═══════════════════ Right Panel ═══════════════════ */}
        <section className="flex-1 flex flex-col bg-background overflow-hidden min-w-0">
          {/* Header */}
          <header className="p-5 border-b border-border flex justify-between items-center shrink-0">
            <div>
              <h1 className="text-xl font-bold text-foreground">Episode Tracking</h1>
              <p className="text-sm text-muted-foreground mt-0.5">
                Downloaded <span className="font-bold text-foreground">{downloaded}/{totalEps}</span> episodes
              </p>
            </div>
            <button
              className="p-2 text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
              onClick={onClose}
              aria-label="Close"
            >
              <svg className="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path d="M6 18L18 6M6 6l12 12" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" />
              </svg>
            </button>
          </header>

          {/* Loading / Empty states */}
          {loading && (
            <div className="flex flex-col items-center gap-3 py-16 flex-1">
              <div className="w-8 h-8 border-4 border-primary/30 border-t-primary rounded-full animate-spin" />
              <p className="text-sm text-muted-foreground">Loading...</p>
            </div>
          )}

          {!loading && !data && (
            <div className="flex-1 flex items-center justify-center">
              <p className="text-sm text-muted-foreground">Failed to load history</p>
            </div>
          )}

          {/* Missing episodes alert */}
          {!loading && data && missing.length > 0 && (
            <div className="px-5 py-3 bg-destructive/10 border-b border-destructive/20 flex items-center justify-between shrink-0">
              <div className="flex items-center gap-2">
                <svg className="h-5 w-5 text-destructive shrink-0" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
                </svg>
                <span className="text-sm font-medium text-destructive">
                  Missing: EP{missing.join(', EP')}
                </span>
              </div>
            </div>
          )}

          {/* Episode table */}
          {!loading && data && (
            <div className="flex-1 overflow-y-auto">
              <table className="w-full text-left border-collapse">
                <thead className="sticky top-0 bg-background z-10">
                  <tr className="text-[10px] uppercase tracking-widest text-muted-foreground border-b border-border">
                    <th className="px-5 py-3 font-semibold">Episode</th>
                    <th className="px-5 py-3 font-semibold">Status</th>
                    <th className="px-5 py-3 font-semibold w-48">Progress</th>
                    <th className="px-5 py-3 font-semibold">Source</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/50">
                  {/* Downloaded episodes */}
                  {sortedEps.map((e) => {
                    const q = e.qbit;
                    const st = statusInfo(q?.state);
                    const progress = q ? Math.round(q.progress * 100) : 0;
                    return (
                      <tr key={e.sort} className="hover:bg-muted/30 transition-colors">
                        <td className="px-5 py-3">
                          <div className="text-sm font-bold text-foreground tabular-nums">
                            EP{e.sort.toString().padStart(2, '0')}
                          </div>
                          {q?.name && (
                            <div className="text-[10px] text-muted-foreground max-w-48 truncate" title={q.name}>
                              {q.name.slice(0, 40)}
                            </div>
                          )}
                        </td>
                        <td className="px-5 py-3">
                          <div className={`flex items-center text-xs font-medium ${st.textColor}`}>
                            <span className={`inline-block w-2 h-2 rounded-full mr-1.5 shrink-0 ${st.color}`} />
                            {st.label}
                          </div>
                        </td>
                        <td className="px-5 py-3 w-48">
                          <div className="w-full bg-muted rounded-full h-1.5">
                            <div
                              className={`h-1.5 rounded-full transition-all ${
                                progress >= 100 ? 'bg-emerald-500' : progress > 0 ? 'bg-sky-400' : 'bg-muted-foreground/30'
                              }`}
                              style={{ width: `${progress}%` }}
                            />
                          </div>
                          <div className="text-[10px] text-right mt-1 text-muted-foreground tabular-nums">
                            {progress}%{q?.dlspeed ? ' · ' + formatSpeed(q.dlspeed) : ''}
                          </div>
                        </td>
                        <td className="px-5 py-3">
                          <span className={`text-[11px] px-2 py-0.5 rounded ${
                            e.source === 'primary'
                              ? 'bg-primary/10 text-primary'
                              : 'bg-amber-500/10 text-amber-600 dark:text-amber-400'
                          }`}>
                            {e.source === 'primary' ? 'Primary' : 'Backup'}
                          </span>
                        </td>
                      </tr>
                    );
                  })}

                  {/* Missing episode rows */}
                  {missing.map((sort) => (
                    <tr key={`missing-${sort}`} className="bg-destructive/5 hover:bg-destructive/10 transition-colors">
                      <td className="px-5 py-3">
                        <div className="text-sm font-bold text-foreground/50 tabular-nums">
                          EP{sort.toString().padStart(2, '0')}
                        </div>
                      </td>
                      <td className="px-5 py-3">
                        <div className="flex items-center text-xs font-medium text-destructive">
                          <span className="inline-block w-2 h-2 rounded-full mr-1.5 shrink-0 bg-destructive/40" />
                          Missing
                        </div>
                      </td>
                      <td className="px-5 py-3 w-48">
                        <div className="w-full bg-destructive/10 rounded-full h-1.5 border border-destructive/20 border-dashed" />
                        <div className="text-[10px] text-right mt-1 text-destructive/60">Not found in feed</div>
                      </td>
                      <td className="px-5 py-3">
                        <span className="text-[11px] text-muted-foreground/50">N/A</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Footer */}
          {!loading && data && (
            <footer className="p-5 border-t border-border flex justify-between items-center shrink-0">
              <span className="text-xs text-muted-foreground">
                Total: {formatBytes(totalSize) || '...'}
              </span>
              <button
                className="bg-primary text-primary-foreground px-6 py-2 rounded-lg text-sm font-semibold shadow-sm hover:shadow-md active:scale-95 transition-all cursor-pointer"
                onClick={onClose}
              >
                Close
              </button>
            </footer>
          )}
        </section>
      </div>
    </div>
  );
}
