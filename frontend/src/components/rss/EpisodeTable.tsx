import type { DownloadHistoryResponse, EpisodeHistoryEntry, SubscriptionOut } from '@/types/preview';

/* ── Status helpers ─────────────────────────────────────────── */

export function statusInfo(state: string | undefined) {
  if (!state) return { label: 'Unknown', color: 'bg-muted-foreground', textColor: 'text-muted-foreground' };
  const map: Record<string, { label: string; color: string; textColor: string }> = {
    uploading:    { label: 'Seeding',       color: 'bg-emerald-500', textColor: 'text-emerald-600' },
    stalledUP:    { label: 'Seeding',       color: 'bg-emerald-500', textColor: 'text-emerald-600' },
    downloading:  { label: 'Downloading',   color: 'bg-sky-400',     textColor: 'text-sky-600' },
    pausedDL:     { label: 'Paused',        color: 'bg-amber-400',   textColor: 'text-amber-600' },
    pausedUP:     { label: 'Paused',        color: 'bg-amber-400',   textColor: 'text-amber-600' },
    queuedDL:     { label: 'Queued',        color: 'bg-amber-400',   textColor: 'text-amber-500' },
    queuedUP:     { label: 'Queued',        color: 'bg-amber-400',   textColor: 'text-amber-500' },
    missingFiles: { label: 'Missing Files', color: 'bg-red-500',     textColor: 'text-red-600' },
  };
  return map[state] || { label: state, color: 'bg-muted-foreground', textColor: 'text-muted-foreground' };
}

export function formatBytes(bytes: number | undefined): string {
  if (!bytes || bytes <= 0) return '';
  if (bytes >= 1073741824) return (bytes / 1073741824).toFixed(1) + ' GB';
  if (bytes >= 1048576) return (bytes / 1048576).toFixed(1) + ' MB';
  if (bytes >= 1024) return (bytes / 1024).toFixed(0) + ' KB';
  return bytes + ' B';
}

export function formatSpeed(bytesPerSec: number | undefined): string {
  if (!bytesPerSec || bytesPerSec <= 0) return '';
  return formatBytes(bytesPerSec) + '/s';
}

/* ── Source badge ───────────────────────────────────────────── */

function sourceBadge(source: string) {
  const map: Record<string, { label: string; cls: string }> = {
    primary: { label: 'Primary', cls: 'bg-primary/10 text-primary' },
    backup:  { label: 'Backup',  cls: 'bg-amber-500/10 text-amber-600 dark:text-amber-400' },
    edit:    { label: 'Edit',    cls: 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400' },
    add:     { label: 'Add',     cls: 'bg-sky-500/10 text-sky-600 dark:text-sky-400' },
  };
  return map[source] || { label: source, cls: 'bg-muted text-muted-foreground' };
}

/* ── Component ──────────────────────────────────────────────── */

interface Props {
  data: DownloadHistoryResponse | null;
  loading: boolean;
  sub: SubscriptionOut | null;
  fileInputRef: React.RefObject<HTMLInputElement | null>;
  fileIntentRef: React.MutableRefObject<{ type: 'add' | 'edit'; sort: number } | null>;
  expandedSort: number | null;
  tmdbForm: { ep: string; season: string };
  setTmdbForm: React.Dispatch<React.SetStateAction<{ ep: string; season: string }>>;
  onOpenTmdb: (sort: number, entry: EpisodeHistoryEntry) => void;
  onSaveTmdb: (sort: number, regen: boolean) => void;
  onDeleteEpisode: (sort: number) => void;
  onClose: () => void;
}

export default function EpisodeTable({
  data, loading, sub, fileInputRef, fileIntentRef,
  expandedSort, tmdbForm, setTmdbForm, onOpenTmdb, onSaveTmdb, onDeleteEpisode, onClose,
}: Props) {
  if (loading) {
    return (
      <div className="flex flex-col items-center gap-3 py-16 flex-1">
        <div className="w-8 h-8 border-4 border-primary/30 border-t-primary rounded-full animate-spin" />
        <p className="text-sm text-muted-foreground">Loading...</p>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <p className="text-sm text-muted-foreground">Failed to load history</p>
      </div>
    );
  }

  const sortedEps = [...data.episodes].sort((a, b) => a.sort - b.sort);
  const missing = data.missing_sorts || [];
  const totalSize = sortedEps.reduce((sum, e) => sum + (e.qbit?.size || 0), 0);
  const totalEps = data.bgm_sortrange[1] - data.bgm_sortrange[0] + 1;
  const downloaded = data.episodes.length;

  return (
    <div className="flex-1 overflow-y-auto">
      {/* Missing alert */}
      {missing.length > 0 && (
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

      <table className="w-full text-left border-collapse">
        <thead className="sticky top-0 bg-background z-10">
          <tr className="text-[10px] uppercase tracking-widest text-muted-foreground border-b border-border">
            <th className="px-5 py-3 font-semibold">Episode</th>
            <th className="px-5 py-3 font-semibold">Status</th>
            <th className="px-5 py-3 font-semibold w-48">Progress</th>
            <th className="px-5 py-3 font-semibold">Source</th>
            <th className="px-5 py-3 font-semibold w-24">Actions</th>
            <th className="px-5 py-3 font-semibold w-8">TMDB</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border/50">
          {/* Downloaded episodes */}
          {sortedEps.map((e) => {
            const q = e.qbit;
            const st = statusInfo(q?.state);
            const progress = q ? Math.round(q.progress * 100) : 0;
            const sb = sourceBadge(e.source);
            const isExpanded = expandedSort === e.sort;
            return (
              <>
                <tr key={`ep-${e.sort}`} className="hover:bg-muted/30 transition-colors">
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
                    <span className={`text-[11px] px-2 py-0.5 rounded ${sb.cls}`}>{sb.label}</span>
                  </td>
                  <td className="px-5 py-3">
                    <div className="flex items-center gap-1.5">
                      <button
                        className="text-[10px] px-2 py-0.5 rounded border border-border text-muted-foreground hover:text-foreground hover:bg-muted transition-colors cursor-pointer"
                        onClick={() => { fileIntentRef.current = { type: 'edit', sort: e.sort }; fileInputRef.current?.click(); }}
                        title="Replace with new .torrent"
                      >Edit</button>
                      <button
                        className="text-[10px] px-2 py-0.5 rounded border border-destructive/30 text-destructive hover:bg-destructive hover:text-destructive-foreground transition-colors cursor-pointer"
                        onClick={() => onDeleteEpisode(e.sort)}
                        title="Remove from history"
                      >Del</button>
                    </div>
                  </td>
                  <td className="px-5 py-3">
                    <button
                      className={`text-xs w-6 h-6 rounded border border-border transition-colors cursor-pointer flex items-center justify-center ${
                        isExpanded
                          ? 'bg-primary/10 text-primary border-primary/30'
                          : 'text-muted-foreground hover:text-foreground hover:bg-muted'
                      }`}
                      onClick={() => onOpenTmdb(e.sort, e)}
                      title="TMDB info"
                    >
                      <span style={{ transform: isExpanded ? 'rotate(180deg)' : 'rotate(0deg)', transition: 'transform 0.15s', display: 'inline-block', fontSize: '10px', lineHeight: 1 }}>▾</span>
                    </button>
                  </td>
                </tr>
                {/* TMDB edit row — rendered inline immediately below its parent episode */}
                {isExpanded && (
                  <tr key={`tmdb-${e.sort}`} className="bg-muted/20">
                    <td colSpan={6} className="px-5 py-2.5">
                      <div className="flex items-center gap-3 text-xs">
                        <span className="text-muted-foreground shrink-0">TMDB:</span>
                        <label className="flex items-center gap-1">
                          <span className="text-muted-foreground">S</span>
                          <input value={tmdbForm.season}
                            onChange={ev => setTmdbForm(p => ({ ...p, season: ev.target.value }))}
                            placeholder={String(sub?.tmdb_season ?? 1)}
                            className="w-10 text-xs bg-background border border-border rounded px-1.5 py-0.5 text-center focus:outline-none focus:border-primary"
                          />
                        </label>
                        <label className="flex items-center gap-1">
                          <span className="text-muted-foreground">EP</span>
                          <input value={tmdbForm.ep}
                            onChange={ev => setTmdbForm(p => ({ ...p, ep: ev.target.value }))}
                            placeholder={String(e.sort)}
                            className="w-10 text-xs bg-background border border-border rounded px-1.5 py-0.5 text-center focus:outline-none focus:border-primary"
                          />
                        </label>
                        <span className="text-[10px] text-muted-foreground ml-1">
                          {tmdbForm.season ? `S${tmdbForm.season}` : `S${sub?.tmdb_season ?? 1}`}
                          {tmdbForm.ep ? `E${tmdbForm.ep}` : `E${e.sort}`}
                        </span>
                        <div className="flex gap-1.5 ml-auto">
                          <button
                            className="text-[10px] px-2 py-0.5 rounded border border-border text-muted-foreground hover:text-foreground hover:bg-muted transition-colors cursor-pointer"
                            onClick={() => onSaveTmdb(e.sort, false)}
                          >Save</button>
                          <button
                            className="text-[10px] px-2 py-0.5 rounded border border-primary/30 text-primary hover:bg-primary hover:text-primary-foreground transition-colors cursor-pointer"
                            onClick={() => onSaveTmdb(e.sort, true)}
                          >Save + NFO</button>
                        </div>
                      </div>
                    </td>
                  </tr>
                )}
              </>
            );
          })}

          {/* Missing rows */}
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
              <td className="px-5 py-3">
                <button
                  className="text-[10px] px-2 py-0.5 rounded border border-primary/30 text-primary hover:bg-primary hover:text-primary-foreground transition-colors cursor-pointer"
                  onClick={() => { fileIntentRef.current = { type: 'add', sort }; fileInputRef.current?.click(); }}
                  title="Upload .torrent to add episode"
                >Add</button>
              </td>
              <td className="px-5 py-3" />
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
