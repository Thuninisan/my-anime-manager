import type { DownloadHistoryResponse, SubscriptionOut } from '@/types/preview';

/* ── Metadata row helper ────────────────────────────────────── */

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex justify-between items-baseline gap-2">
      <span className="text-[10px] text-muted-foreground shrink-0">{label}</span>
      <span className={`text-xs font-medium text-foreground text-right truncate ${mono ? 'font-mono text-[10px]' : ''}`} title={value}>
        {value}
      </span>
    </div>
  );
}

/* ── Component ──────────────────────────────────────────────── */

interface Props {
  sub: SubscriptionOut | null;
  data: DownloadHistoryResponse | null;
  editingCard: 'primary' | 'backup' | null;
  editingExclude: string;
  deleteType: 'primary' | 'backup' | null;
  onStartEdit: (type: 'primary' | 'backup') => void;
  onExcludeChange: (val: string) => void;
  onSaveExclude: () => void;
  onSetDeleteType: (type: 'primary' | 'backup' | null) => void;
  onConfirmDelete: () => void;
}

export default function LeftSidebar({
  sub, data,
  editingCard, editingExclude, deleteType,
  onStartEdit, onExcludeChange, onSaveExclude,
  onSetDeleteType, onConfirmDelete,
}: Props) {
  const isActive = sub?.active !== 0;

  return (
    <aside className="w-full md:w-72 bg-muted/40 border-r border-border flex flex-col shrink-0">
      {/* Poster */}
      <div className="p-5">
        <div className="relative aspect-[3/4] rounded-lg overflow-hidden shadow-sm">
          {sub?.poster_url ? (
            <img src={sub.poster_url} alt={sub.name} className="w-full h-full object-cover" />
          ) : (
            <div className="w-full h-full"
              style={{
                background: `linear-gradient(135deg, hsl(${(sub?.bangumi_id || 1) * 137 % 360},45%,35%), hsl(${((sub?.bangumi_id || 1) * 137 + 40) % 360},35%,20%))`,
              }}
            >
              <div className="absolute inset-0 flex items-center justify-center">
                <span className="text-4xl font-bold text-white/25">
                  {(sub?.name || data?.name || '?')[0]}
                </span>
              </div>
            </div>
          )}
          {(sub?.bgm_rating != null && sub.bgm_rating > 0) && (
            <div className="absolute top-3 left-3 bg-secondary text-white text-[10px] font-bold px-2 py-1 rounded-full shadow-sm">
              BGM {sub.bgm_rating.toFixed(1)} / 10
            </div>
          )}
        </div>
      </div>

      {/* Metadata */}
      <div className="px-5 pb-5 flex-1 overflow-y-auto">
        <h2 className="text-lg font-bold text-foreground leading-tight">
          {sub?.name || data?.name || '...'}
        </h2>
        <p className="text-sm text-muted-foreground mt-1 flex items-center gap-2">
          Season {data?.bgm_season || sub?.bgm_season || '?'}
          {sub?.backup_subgroup_name && (
            <>
              <span className="text-muted-foreground/40">·</span>
              <span className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium ${
                isActive
                  ? 'bg-amber-500/10 text-amber-600'
                  : 'bg-emerald-500/10 text-emerald-600'
              }`}>
                <span className={`w-1.5 h-1.5 rounded-full ${isActive ? 'bg-amber-500' : 'bg-emerald-500'}`} />
                {isActive ? 'Ongoing' : 'Completed'}
              </span>
            </>
          )}
        </p>

        {/* Status card — only when single RSS (backup absent, more room) */}
        {!sub?.backup_subgroup_name && (
          <div className="mt-3 my-4">
            <span className="text-[13px] uppercase tracking-wider text-muted-foreground font-semibold block mb-1.5">Status</span>
            <span className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-sm font-semibold ${
              isActive
                ? 'bg-amber-500/10 text-amber-600'
                : 'bg-emerald-500/10 text-emerald-600'
            }`}>
              <span className={`w-2 h-2 rounded-full ${isActive ? 'bg-amber-500' : 'bg-emerald-500'}`} />
              {isActive ? 'Ongoing' : 'Completed'}
            </span>
          </div>
        )}

        <div className={sub?.backup_subgroup_name ? 'mt-4 space-y-2.5' : 'mt-4 space-y-2.5'}>
          {/* ── Primary RSS card ── */}
          <div className="relative group bg-muted/30 rounded-lg p-3 space-y-1.5">
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Primary RSS</div>
            <Row label="Sub Group" value={sub?.subgroup_name || '—'} />
            <Row label="Tags" value={sub?.filter_tags?.length ? sub.filter_tags.join(', ') : '无'} />
            {editingCard === 'primary' ? (
              <div className="flex justify-between items-baseline gap-2">
                <span className="text-[10px] text-muted-foreground shrink-0">Exclude</span>
                <input
                  value={editingExclude}
                  onChange={e => onExcludeChange(e.target.value)}
                  onBlur={onSaveExclude}
                  onKeyDown={e => { if (e.key === 'Enter') onSaveExclude(); }}
                  className="text-xs bg-background border border-border rounded px-1.5 py-0.5 w-24 text-right focus:outline-none focus:border-primary"
                  autoFocus
                />
              </div>
            ) : (
              <Row label="Exclude" value={sub?.exclude_patterns?.length ? sub.exclude_patterns.join(', ') : '无'} />
            )}
            <Row label="RSS" value={sub?.rss_url || '—'} mono />

            {editingCard !== 'primary' && (
              <div className="absolute inset-0 rounded-lg bg-primary/5 backdrop-blur-[1px] opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center gap-3">
                <button className="bg-card text-foreground p-2.5 rounded-full shadow-md hover:bg-accent hover:text-accent-foreground transition-colors cursor-pointer"
                  onClick={(e) => { e.stopPropagation(); onStartEdit('primary'); }}
                  title="Edit exclude patterns"
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/>
                  </svg>
                </button>
                <button className="bg-card text-destructive p-2.5 rounded-full shadow-md hover:bg-destructive hover:text-destructive-foreground transition-colors cursor-pointer"
                  onClick={(e) => { e.stopPropagation(); onSetDeleteType('primary'); }}
                  title="Delete primary RSS"
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/>
                  </svg>
                </button>
              </div>
            )}
          </div>

          {/* ── Backup RSS card ── */}
          {sub?.backup_subgroup_name && (
            <div className="relative group bg-muted/30 rounded-lg p-3 space-y-1">
              <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Backup RSS</div>
              <Row label="Sub Group" value={sub.backup_subgroup_name} />
              <Row label="Tags" value={sub?.backup_filter_tags?.length ? sub.backup_filter_tags.join(', ') : '无'} />
              {editingCard === 'backup' ? (
                <div className="flex justify-between items-baseline gap-2">
                  <span className="text-[10px] text-muted-foreground shrink-0">Exclude</span>
                  <input
                    value={editingExclude}
                    onChange={e => onExcludeChange(e.target.value)}
                    onBlur={onSaveExclude}
                    onKeyDown={e => { if (e.key === 'Enter') onSaveExclude(); }}
                    className="text-xs bg-background border border-border rounded px-1.5 py-0.5 w-24 text-right focus:outline-none focus:border-primary"
                    autoFocus
                  />
                </div>
              ) : (
                <Row label="Exclude" value={sub?.backup_exclude_patterns?.length ? sub.backup_exclude_patterns.join(', ') : '无'} />
              )}
              <Row label="RSS" value={sub.backup_rss_url || '—'} mono />

              {editingCard !== 'backup' && (
                <div className="absolute inset-0 rounded-lg bg-primary/5 backdrop-blur-[1px] opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center gap-3">
                  <button className="bg-card text-foreground p-2.5 rounded-full shadow-md hover:bg-accent hover:text-accent-foreground transition-colors cursor-pointer"
                    onClick={(e) => { e.stopPropagation(); onStartEdit('backup'); }}
                    title="Edit backup exclude patterns"
                  >
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/>
                    </svg>
                  </button>
                  <button className="bg-card text-destructive p-2.5 rounded-full shadow-md hover:bg-destructive hover:text-destructive-foreground transition-colors cursor-pointer"
                    onClick={(e) => { e.stopPropagation(); onSetDeleteType('backup'); }}
                    title="Delete backup RSS"
                  >
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/>
                  </svg>
                </button>
              </div>
            )}
          </div>
          )}

        </div>
      </div>

      {/* Edit TMDB — fixed at bottom */}
      <div className="p-3 border-t border-border shrink-0">
        <button className="w-full flex items-center justify-center gap-2 px-4 py-2 bg-primary/10 text-primary hover:bg-primary/20 transition-colors rounded-full text-sm font-bold cursor-pointer">
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2"/>
          </svg>
          Edit TMDB
        </button>
      </div>
    </aside>
  );
}
