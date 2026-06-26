import { useState, useEffect, useRef, useCallback } from 'react';
import type { DownloadHistoryResponse, SubscriptionOut, SeasonInfo } from '@/types/preview';
import { updateSubscription, deleteSubscriptionRss, deleteEpisodeHistory, addEpisodeWithTorrent, replaceEpisodeWithTorrent, getTmdbSeasonMap } from '@/api/rssApi';
import LeftSidebar from './LeftSidebar';
import EpisodeTable, { formatBytes } from './EpisodeTable';

interface Props {
  open: boolean;
  data: DownloadHistoryResponse | null;
  loading: boolean;
  subscription: SubscriptionOut | null;
  onClose: () => void;
  onRefresh: () => void;
}

export default function DownloadHistoryDialog({ open, data, loading, subscription, onClose, onRefresh }: Props) {
  // ── Local subscription state ──
  const [sub, setSub] = useState<SubscriptionOut | null>(subscription);
  useEffect(() => { setSub(subscription); }, [subscription]);

  const idRef = useRef<number | null>(null);
  useEffect(() => { if (sub) idRef.current = sub.bangumi_id; }, [sub]);

  // ── Fetch TMDB season/episode map on open ──
  const [tmdbSeasonMap, setTmdbSeasonMap] = useState<Record<string, SeasonInfo> | null>(null);
  useEffect(() => {
    if (!open || !sub?.tmdb_id) { setTmdbSeasonMap(null); return; }
    let cancelled = false;
    getTmdbSeasonMap(sub.tmdb_id)
      .then(data => { if (!cancelled) setTmdbSeasonMap(data); })
      .catch(() => { if (!cancelled) setTmdbSeasonMap(null); })
    return () => { cancelled = true; };
  }, [open, sub?.tmdb_id]);

  // ── Card editing ──
  const [editingCard, setEditingCard] = useState<'primary' | 'backup' | null>(null);
  const [editingExclude, setEditingExclude] = useState('');

  const startEdit = (type: 'primary' | 'backup') => {
    setEditingCard(type);
    const patterns = type === 'primary' ? sub?.exclude_patterns : sub?.backup_exclude_patterns;
    setEditingExclude(patterns?.join(', ') ?? '');
  };

  const saveExclude = async () => {
    if (!idRef.current || !sub || !editingCard) { setEditingCard(null); return; }
    const patterns = editingExclude.split(',').map(s => s.trim()).filter(Boolean);
    const field = editingCard === 'primary' ? 'exclude_patterns' : 'backup_exclude_patterns';
    try { await updateSubscription(idRef.current, { [field]: patterns }); setSub(prev => prev ? { ...prev, [field]: patterns } : prev); } catch { /* */ }
    setEditingCard(null);
  };

  // ── RSS card delete ──
  const [deleteType, setDeleteType] = useState<'primary' | 'backup' | null>(null);

  const handleDeleteRss = async () => {
    if (!idRef.current || !deleteType) return;
    try {
      const { deleted } = await deleteSubscriptionRss(idRef.current, deleteType);
      if (deleted) { setDeleteType(null); onClose(); return; }
      if (deleteType === 'primary') {
        setSub(prev => prev ? { ...prev, rss_url: '', subgroup_id: 0, subgroup_name: '', filter_tags: [], exclude_patterns: [] } : prev);
      } else {
        setSub(prev => prev ? { ...prev, backup_rss_url: '', backup_subgroup_id: 0, backup_subgroup_name: '', backup_filter_tags: [], backup_exclude_patterns: [] } : prev);
      }
    } catch { /* */ }
    setDeleteType(null);
  };

  // ── Episode actions ──
  const fileInputRef = useRef<HTMLInputElement>(null);
  const fileIntentRef = useRef<{ type: 'add' | 'edit'; sort: number } | null>(null);

  const handleDeleteEpisode = async (sort: number) => {
    if (!data) return;
    try { await deleteEpisodeHistory(data.bangumi_id, sort); onRefresh(); } catch { /* */ }
  };

  // ── Replace confirmation ──
  const [replaceDialog, setReplaceDialog] = useState<{ sort: number; file: File } | null>(null);

  const confirmReplace = useCallback(async () => {
    const rd = replaceDialog;
    if (!rd || !data) return;
    setReplaceDialog(null);
    try { await replaceEpisodeWithTorrent(data.bangumi_id, rd.sort, rd.file); onRefresh(); } catch { /* */ }
  }, [replaceDialog, data, onRefresh]);

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    const intent = fileIntentRef.current;
    if (!file || !intent || !data) return;
    if (fileInputRef.current) fileInputRef.current.value = '';
    fileIntentRef.current = null;
    if (intent.type === 'edit') {
      setReplaceDialog({ sort: intent.sort, file });
    } else {
      try { await addEpisodeWithTorrent(data.bangumi_id, intent.sort, file); onRefresh(); } catch { /* */ }
    }
  };

  // ── TMDB overrides ──
  const [expandedSort, setExpandedSort] = useState<number | null>(null);
  const [tmdbForm, setTmdbForm] = useState<{ ep: string; season: string }>({ ep: '', season: '' });

  const openTmdbDropdown = (sort: number, entry: { tmdb_ep?: number | null; tmdb_season?: number | null }) => {
    setExpandedSort(sort === expandedSort ? null : sort);

    // Smart defaults: existing override → TMDB match → subscription default → bare fallback
    let season = entry.tmdb_season != null ? String(entry.tmdb_season) : '';
    let ep = entry.tmdb_ep != null ? String(entry.tmdb_ep) : '';

    if (tmdbSeasonMap) {
      // Filter out S00 (Specials) from default candidates
      const seasonKeys = Object.keys(tmdbSeasonMap).filter(k => k !== '0').sort((a, b) => Number(a) - Number(b));
      if (!season) {
        season = sub?.tmdb_season != null ? String(sub.tmdb_season) : (seasonKeys[0] || '');
      }
      if (season && tmdbSeasonMap[season] && !ep) {
        const seasonEps = tmdbSeasonMap[season].episodes;
        if (seasonEps.some(e => e.epNum === sort)) {
          ep = String(sort);
        } else if (seasonEps.length > 0) {
          ep = String(seasonEps[0].epNum);
        }
      }
    }

    // Fallback defaults — applies regardless of whether TMDB data is available,
    // uses the same values shown as input placeholders
    if (!season) season = String(sub?.tmdb_season ?? 1);
    if (!ep) ep = String(sort);

    setTmdbForm({ ep, season });
  };

  const saveTmdbOverrides = async (sort: number, regen: boolean) => {
    if (!data) return;
    const fields: Record<string, number> = {};
    if (tmdbForm.ep !== '') fields.tmdb_ep = Number(tmdbForm.ep);
    if (tmdbForm.season !== '') fields.tmdb_season = Number(tmdbForm.season);
    if (Object.keys(fields).length === 0) return;
    try {
      await fetch(`/api/rss/download-history/${data.bangumi_id}/${sort}${regen ? '?regen_nfo=true' : ''}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(fields),
      });
      onRefresh();
      setExpandedSort(null);
    } catch { /* */ }
  };

  // ── Render ──
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-sm">
      <div className="bg-card w-full max-w-6xl h-[85vh] rounded-xl shadow-2xl overflow-hidden flex flex-col md:flex-row">

        <LeftSidebar
          sub={sub} data={data}
          editingCard={editingCard} editingExclude={editingExclude}
          deleteType={deleteType}
          onStartEdit={startEdit} onExcludeChange={setEditingExclude}
          onSaveExclude={saveExclude}
          onSetDeleteType={setDeleteType} onConfirmDelete={handleDeleteRss}
        />

        <section className="flex-1 flex flex-col bg-background overflow-hidden min-w-0">
          <header className="p-5 border-b border-border flex justify-between items-center shrink-0">
            <div>
              <h1 className="text-xl font-bold text-foreground">Episode Tracking</h1>
              <p className="text-sm text-muted-foreground mt-0.5">
                Downloaded{' '}
                <span className="font-bold text-foreground">
                  {data ? data.episodes.length : 0}/{data ? data.bgm_sortrange[1] - data.bgm_sortrange[0] + 1 : 0}
                </span>{' '}
                episodes
              </p>
            </div>
            <button className="p-2 text-muted-foreground hover:text-foreground transition-colors cursor-pointer" onClick={onClose} aria-label="Close">
              <svg className="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path d="M6 18L18 6M6 6l12 12" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" />
              </svg>
            </button>
          </header>

          <EpisodeTable
            data={data} loading={loading} sub={sub}
            fileInputRef={fileInputRef} fileIntentRef={fileIntentRef}
            expandedSort={expandedSort} tmdbForm={tmdbForm} setTmdbForm={setTmdbForm}
            tmdbSeasonMap={tmdbSeasonMap}
            onOpenTmdb={openTmdbDropdown} onSaveTmdb={saveTmdbOverrides}
            onDeleteEpisode={handleDeleteEpisode}
            onClose={onClose}
          />

          {!loading && data && (
            <footer className="p-4 border-t border-border flex justify-between items-center shrink-0">
              <span className="text-xs text-muted-foreground">
                Total: {formatBytes(data.episodes.reduce((sum, e) => sum + (e.qbit?.size || 0), 0)) || '...'}
              </span>
              <button
                className="bg-primary text-primary-foreground px-6 py-2 rounded-lg text-sm font-semibold shadow-sm hover:shadow-md active:scale-95 transition-all cursor-pointer"
                onClick={onClose}
              >Close</button>
            </footer>
          )}
        </section>
      </div>

      {/* ── Delete RSS confirmation ── */}
      {deleteType && (
        <div className="absolute inset-0 z-10 flex items-center justify-center bg-black/50 rounded-xl" onClick={() => setDeleteType(null)}>
          <div className="bg-card rounded-xl p-6 shadow-2xl max-w-sm mx-4" onClick={e => e.stopPropagation()}>
            <h3 className="text-lg font-bold text-foreground">Delete {deleteType === 'primary' ? 'Primary' : 'Backup'} RSS?</h3>
            <p className="text-sm text-muted-foreground mt-2">
              {deleteType === 'primary'
                ? 'This will clear the primary RSS subscription (subgroup, tags, and exclude patterns).'
                : 'This will clear the backup RSS subscription.'}
              {' '}If this is the only RSS source, the entire subscription will be removed.
            </p>
            <div className="flex gap-3 mt-5 justify-end">
              <button className="px-4 py-2 border border-border text-muted-foreground rounded-lg text-sm font-medium hover:bg-muted transition-colors cursor-pointer" onClick={() => setDeleteType(null)}>Cancel</button>
              <button className="px-4 py-2 bg-destructive text-destructive-foreground rounded-lg text-sm font-semibold hover:bg-destructive/90 transition-colors cursor-pointer" onClick={handleDeleteRss}>Delete</button>
            </div>
          </div>
        </div>
      )}

      {/* ── Replace confirmation ── */}
      {replaceDialog && (
        <div className="absolute inset-0 z-10 flex items-center justify-center bg-black/50 rounded-xl" onClick={() => setReplaceDialog(null)}>
          <div className="bg-card rounded-xl p-6 shadow-2xl max-w-sm mx-4" onClick={e => e.stopPropagation()}>
            <h3 className="text-lg font-bold text-foreground">Replace EP{replaceDialog.sort.toString().padStart(2, '0')}?</h3>
            <p className="text-sm text-muted-foreground mt-2">
              This will delete the existing torrent and replace it with{' '}
              <span className="font-medium text-foreground">{replaceDialog.file.name}</span>.
              The new episode will be recorded as <span className="font-medium text-foreground">source: edit</span>.
            </p>
            <div className="flex gap-3 mt-5 justify-end">
              <button className="px-4 py-2 border border-border text-muted-foreground rounded-lg text-sm font-medium hover:bg-muted transition-colors cursor-pointer" onClick={() => setReplaceDialog(null)}>Cancel</button>
              <button className="px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-semibold hover:bg-primary/90 transition-colors cursor-pointer" onClick={confirmReplace}>Replace</button>
            </div>
          </div>
        </div>
      )}

      <input ref={fileInputRef} type="file" accept=".torrent" className="hidden" onChange={handleFileChange} />
    </div>
  );
}
