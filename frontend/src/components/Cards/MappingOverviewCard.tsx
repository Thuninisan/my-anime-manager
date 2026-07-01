import { useState, useCallback, useMemo, useRef } from 'react';
import type { TorrentPreviewResponse, EpisodeBlock } from '../../types/preview';
import EpisodeEditSheet from './EpisodeEditSheet';
import { uploadSubtitle } from '../../api/torrentApi';

/* ======== Constants ======== */
const EXTRA_KEY_BASE = 900;

// Allowed subtitle file extensions for upload validation
const ALLOWED_SUB_EXTENSIONS = ['.ass', '.ssa', '.srt', '.sub', '.idx', '.vtt', '.ttml', '.sbv', '.dfxp'];
// Corresponding MIME types and extensions for the file input accept attribute
const SUB_ACCEPT = '.ass,.ssa,.srt,.sub,.idx,.vtt,.ttml,.sbv,.dfxp';

/* ======== Helpers ======== */
function sanitizeDirName(name: string): string {
  return name.replace(/[<>:"/\\|?*]/g, '');
}
function findSeasonNumber(bgm: Record<string, { name: string }>, name: string): number | null {
  for (const [k, v] of Object.entries(bgm)) if (v.name === name) return parseInt(k, 10);
  return null;
}
function buildNewPath(ep: EpisodeBlock, sn: number, subject: string, epNum: number, fallback: string): string {
  const old = ep.newPath || '';
  const idx = old.indexOf(`/Season ${ep.season_number}/`);
  const dir = idx >= 0 ? old.substring(0, idx) : sanitizeDirName(fallback);
  const ext = ep.oldPath.substring(ep.oldPath.lastIndexOf('.'));
  if (sn === 0 || sn >= EXTRA_KEY_BASE) {
    const label = sn === 0 ? `S00E${String(epNum).padStart(2, '0')}` : `E${String(epNum).padStart(2, '0')}`;
    return `${dir}/Specials/${sanitizeDirName(subject)} ${label}${ext}`;
  }
  return `${dir}/Season ${sn}/${sanitizeDirName(subject)} ${String(epNum).padStart(2, '0')}${ext}`;
}

/* ======== Icons ======== */
function IconEdit() {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
      <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
    </svg>
  );
}
function IconChevronDown() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="6 9 12 15 18 9" />
    </svg>
  );
}
function IconDelete() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
    </svg>
  );
}

/* ======== Props ======== */
interface Props { data: TorrentPreviewResponse; }

/* ======== Episode Card — 1:1 template ======== */
function EpisodeCard({
  filename, ep, hue, matchRate, onEdit,
  subtitles, torrentName, onSubtitleUploaded,
}: {
  filename: string; ep: EpisodeBlock; hue: number; matchRate: 'matched' | 'partial' | 'none';
  onEdit: (f: string) => void;
  subtitles: string[];
  torrentName: string;
  onSubtitleUploaded: (filename: string) => void;
}) {
  const isSpecial = ep.season_number === 0 || ep.season_number >= EXTRA_KEY_BASE;
  const ext = filename.split('.').pop()?.toUpperCase();
  const borderColor = matchRate === 'matched' ? 'border-l-accent' : matchRate === 'partial' ? 'border-l-warning/50' : 'border-l-destructive/50';
  const opacity = matchRate === 'none' ? 'opacity-70' : '';

  // Check whether any subtitle filename stem matches this video's filename stem
  const videoStem = filename.replace(/\.[^.]+$/, '').toLowerCase();
  const hasMatchingSubtitle = subtitles.some(
    (sub) => sub.replace(/\.[^.]+$/, '').toLowerCase() === videoStem,
  );

  // Subtitle upload state
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const handleSubUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    // Client-side extension validation
    const fileExt = '.' + file.name.split('.').pop()?.toLowerCase();
    if (!ALLOWED_SUB_EXTENSIONS.includes(fileExt)) {
      setUploadError(`不支持的字幕格式: ${fileExt}`);
      // Reset input so the same file can be re-selected after fixing
      if (fileInputRef.current) fileInputRef.current.value = '';
      return;
    }

    setUploading(true);
    setUploadError(null);
    try {
      await uploadSubtitle(file, torrentName);
      onSubtitleUploaded(file.name);
    } catch (err: any) {
      setUploadError(err.message || '上传失败');
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  return (
    <div className={`glass-card rounded-xl overflow-hidden sakura-shadow border-l-4 ${borderColor} ${opacity}`}>
      <div className="p-6 flex flex-col md:flex-row gap-6">
        {/* ── Poster area ── */}
        <div
          className="w-full md:w-32 h-44 flex-shrink-0 bg-muted rounded-lg overflow-hidden relative group cursor-pointer"
          style={{ background: `linear-gradient(135deg, hsl(${hue},40%,35%), hsl(${(hue+40)%360},30%,20%))` }}
          onClick={() => onEdit(filename)}
        >
          <div className="absolute inset-0 flex items-center justify-center">
            <span className="text-2xl font-bold text-white/40">
              {(ep.bangumi_subject_name || ep.oldPath)[0]}
            </span>
          </div>
          {/* Hover edit overlay */}
          <div className="absolute inset-0 bg-primary/40 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center">
            <IconEdit />
          </div>
          {/* TMDB badge */}
          {ep.tmdb && (
            <div className="absolute top-2 left-2 px-1.5 py-px bg-secondary text-white text-[10px] font-bold rounded">
              TMDB
            </div>
          )}
          {/* Subtitle badge or upload button */}
          <div className="absolute top-2 right-2">
            {hasMatchingSubtitle ? (
              <span className="bg-[#f09199]/10 text-[#f09199] text-[9px] px-2 py-0.5 rounded-full font-bold uppercase tracking-wider">
                Sub
              </span>
            ) : (
              <>
                {/* Hidden file input for subtitle upload */}
                <input
                  ref={fileInputRef}
                  type="file"
                  accept={SUB_ACCEPT}
                  className="hidden"
                  onChange={handleSubUpload}
                />
                <button
                  className="bg-[#f09199]/10 text-[#f09199] text-[9px] px-2 py-0.5 rounded-full font-bold uppercase tracking-wider hover:bg-[#f09199]/25 transition-colors cursor-pointer disabled:opacity-50"
                  title="上传字幕文件 (.ass, .srt 等)"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={uploading}
                >
                  {uploading ? '...' : '+Sub'}
                </button>
              </>
            )}
          </div>
        </div>

        {/* ── Info area ── */}
        <div className="flex-1 space-y-3">
          <div className="flex justify-between items-start gap-2">
            <div className="min-w-0">
              <h5 className="text-base font-semibold leading-tight truncate" title={filename}>
                {filename}
              </h5>
              <p className="text-sm text-muted-foreground mt-1">
                Detected:{' '}
                <span className="text-primary font-bold">
                  {ep.bangumi_subject_name || 'Unknown'}
                </span>
                {!isSpecial && <> &bull; Season {ep.season_number} &bull; Ep {String(ep.episode_number).padStart(2, '0')}</>}
                {isSpecial && ep.season_number === 0 && <> &bull; Specials</>}
                {isSpecial && ep.season_number >= EXTRA_KEY_BASE && <> &bull; Extra</>}
              </p>
              {/* Subtitle upload error */}
              {uploadError && (
                <p className="text-xs text-destructive mt-1">{uploadError}</p>
              )}
            </div>
            <div className="flex items-center gap-2 shrink-0">
              {ext && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground font-mono">
                  {ext}
                </span>
              )}
              <button className="text-muted-foreground/40 hover:text-destructive transition-colors cursor-pointer">
                <IconDelete />
              </button>
            </div>
          </div>

          {/* ── Selects row ── */}
          <div className="grid grid-cols-2 gap-3 pt-3">
            {/* Provider Association */}
            <div className="space-y-1">
              <label className="text-xs font-semibold text-muted-foreground tracking-wide">
                Provider Association
              </label>
              <div className="relative">
                <select
                  className="w-full bg-muted/50 border border-border/30 rounded-lg px-3 py-1.5 text-sm appearance-none cursor-pointer focus:outline-none focus:ring-2 focus:ring-accent/50"
                  value={ep.tmdb_season}
                  onChange={() => {}}
                >
                  <option>TMDB (S{String(ep.tmdb_season).padStart(2, '0')})</option>
                </select>
                <span className="absolute right-2 top-2 text-muted-foreground pointer-events-none">
                  <IconChevronDown />
                </span>
              </div>
            </div>

            {/* Episode Mapping */}
            <div className="space-y-1">
              <label className="text-xs font-semibold text-muted-foreground tracking-wide">
                Episode Mapping
              </label>
              <div className="flex gap-1">
                <input
                  className="w-full bg-muted/50 border border-border/30 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-accent/50"
                  placeholder="S"
                  type="number"
                  value={ep.season_number}
                  readOnly
                />
                <input
                  className="w-full bg-muted/50 border border-border/30 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-accent/50"
                  placeholder="E"
                  type="number"
                  value={ep.episode_number}
                  readOnly
                />
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* ── Status bar ── */}
      <div className="bg-muted/30 px-6 py-3 flex justify-between items-center border-t border-border/20">
        <div className="flex items-center gap-3">
          <div className="w-32 h-2 bg-muted rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition ${
                matchRate === 'matched' ? 'bg-accent' : matchRate === 'partial' ? 'bg-warning' : 'bg-muted-foreground/30'
              }`}
              style={{ width: matchRate === 'matched' ? '100%' : matchRate === 'partial' ? '50%' : '20%' }}
            />
          </div>
          <span className={`text-[10px] font-bold uppercase tracking-wide ${
            matchRate === 'matched' ? 'text-accent' : matchRate === 'partial' ? 'text-warning' : 'text-muted-foreground'
          }`}>
            {matchRate === 'matched' ? 'READY TO RENAME' : matchRate === 'partial' ? 'NEEDS REVIEW' : 'MANUAL INPUT REQUIRED'}
          </span>
        </div>
        <button
          className="text-xs text-secondary font-semibold tracking-wide hover:underline decoration-2 underline-offset-4 cursor-pointer"
          onClick={() => onEdit(filename)}
        >
          Manual Fix
        </button>
      </div>
    </div>
  );
}

/* ======== Stats Sidebar ======== */
function StatsSidebar({
  total, matched, partial, unmatched, title,
}: {
  total: number; matched: number; partial: number; unmatched: number; title: string;
}) {
  return (
    <div className="space-y-4">
      {/* Stats */}
      <div className="glass-card rounded-xl sakura-shadow p-4 space-y-4">
        <h4 className="text-sm font-semibold flex items-center gap-1.5">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 3v18h18" /><path d="m19 9-5 5-4-4-3 3" />
          </svg>
          Overview
        </h4>
        <div className="grid grid-cols-2 gap-3">
          <div className="bg-muted/50 rounded-lg p-3 text-center">
            <div className="text-2xl font-bold text-primary">{total}</div>
            <div className="text-[10px] text-muted-foreground mt-0.5">Total Files</div>
          </div>
          <div className="bg-muted/50 rounded-lg p-3 text-center">
            <div className="text-2xl font-bold text-secondary">{matched}</div>
            <div className="text-[10px] text-muted-foreground mt-0.5">Ready</div>
          </div>
        </div>
        <div className="space-y-2">
          <div className="flex justify-between"><span className="text-xs text-muted-foreground">Ready</span><span className="text-xs font-medium text-accent">{matched}</span></div>
          <div className="w-full h-1.5 bg-muted rounded-full overflow-hidden">
            <div className="h-full bg-accent rounded-full" style={{ width: `${total>0?(matched/total)*100:0}%` }} />
          </div>
          {partial > 0 && <>
            <div className="flex justify-between"><span className="text-xs text-muted-foreground">Needs Review</span><span className="text-xs font-medium text-warning">{partial}</span></div>
            <div className="w-full h-1.5 bg-muted rounded-full overflow-hidden">
              <div className="h-full bg-warning rounded-full" style={{ width: `${(partial/total)*100}%` }} />
            </div>
          </>}
          {unmatched > 0 && <>
            <div className="flex justify-between"><span className="text-xs text-muted-foreground">Unmatched</span><span className="text-xs font-medium text-destructive">{unmatched}</span></div>
            <div className="w-full h-1.5 bg-muted rounded-full overflow-hidden">
              <div className="h-full bg-destructive/50 rounded-full" style={{ width: `${(unmatched/total)*100}%` }} />
            </div>
          </>}
        </div>
        <div className="text-[11px] text-muted-foreground pt-1 border-t border-border/30">
          <p className="font-medium text-foreground mb-0.5">{title}</p>
          <p>Review each card; use &quot;Manual Fix&quot; to adjust mappings before processing.</p>
        </div>
      </div>

      {/* Atmosphere / decoration */}
      <div className="relative h-48 rounded-xl overflow-hidden group"
        style={{ background: 'linear-gradient(135deg, hsl(355,50%,45%), hsl(5,40%,30%))' }}>
        <div className="absolute inset-0 flex items-center justify-center opacity-30">
          <svg width="80" height="80" viewBox="0 0 24 24" fill="currentColor" className="text-white">
            <path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5c-1.38 0-2.5-1.12-2.5-2.5S10.62 6.5 12 6.5s2.5 1.12 2.5 2.5-1.12 2.5-2.5 2.5z" />
          </svg>
        </div>
        <div className="absolute bottom-3 left-3 right-3">
          <p className="text-white/80 text-xs font-semibold">Sakura Breeze</p>
          <p className="text-white/50 text-[10px]">Intelligent metadata mapping</p>
        </div>
      </div>
    </div>
  );
}

/* ======== Main Component ======== */
export default function MappingOverviewCard({ data }: Props) {
  const [editFilename, setEditFilename] = useState<string | null>(null);
  const [episodes, setEpisodes] = useState(data.episodes);
  const [, setSeasonsState] = useState(data.seasons);

  // User-uploaded subtitle filenames (appended to torrent subtitles for badge display)
  const [uploadedSubtitles, setUploadedSubtitles] = useState<string[]>([]);
  const combinedSubtitles = useMemo(
    () => [...(data.subtitles || []), ...uploadedSubtitles],
    [data.subtitles, uploadedSubtitles],
  );

  const handleSubtitleUploaded = useCallback((filename: string) => {
    setUploadedSubtitles((prev) => [...prev, filename]);
  }, []);

  const tmdbSeasonOptions = useMemo(() => Object.keys(data.tmdb_data || {}).map(Number).sort((a,b)=>a-b), [data.tmdb_data]);
  const bangumiEntryOptions = useMemo(() => Object.entries(data.bangumi_data || {}).map(([k,v])=>({key:Number(k),name:v.name})).sort((a,b)=>a.key-b.key), [data.bangumi_data]);

  const seasonGroups = useMemo(() => {
    const g = new Map<number, EpisodeBlock[]>();
    for (const [,ep] of Object.entries(episodes)) {
      if (!g.has(ep.season_number)) g.set(ep.season_number, []);
      g.get(ep.season_number)!.push(ep);
    }
    return g;
  }, [episodes]);

  const entries = Object.entries(episodes);
  const matched = entries.filter(([,e])=>e.tmdb&&e.bangumi_ep_id).length;
  const partial = entries.filter(([,e])=>e.tmdb&&!e.bangumi_ep_id).length;
  const unmatched = entries.filter(([,e])=>!e.tmdb).length;

  const seasonTmdbMap = useMemo(() => {
    const m: Record<number,number> = {};
    for (const [,ep] of Object.entries(episodes)) m[ep.season_number]=ep.tmdb_season;
    return m;
  }, [episodes]);

  const handleSeasonTmdbChange = useCallback((sn: number, newTs: number) => {
    setEpisodes(prev => {
      const next = {...prev};
      for (const [f,ep] of Object.entries(next)) {
        if (ep.season_number === sn) {
          const td = data.tmdb_data?.[String(newTs)]?.episodes?.[String(ep.episode_number)];
          const tmdb = td ? {name:td.name,overview:td.overview||'',air_date:td.air_date||'',runtime:td.runtime||0,id:td.id,still_path:td.still_path||'',directors:td.directors||[],writers:td.writers||[],guest_stars:td.guest_stars||[]} : null;
          next[f] = {...ep, tmdb_season:newTs, tmdb, newPath: buildNewPath(ep,sn,ep.bangumi_subject_name,ep.episode_number,data.tvshow.title)};
          data.episodes[f] = {...data.episodes[f], tmdb_season:newTs, tmdb, newPath: next[f].newPath};
        }
      }
      return next;
    });
  }, [data]);

  const handleSeasonBangumiChange = useCallback((oldSn: number, newKey: number) => {
    const nb = data.bangumi_data?.[String(newKey)];
    if (!nb || newKey === oldSn) return;
    setEpisodes(prev => {
      const next = {...prev};
      for (const [f,ep] of Object.entries(next)) {
        if (ep.season_number === oldSn) {
          next[f] = {...ep, season_number:newKey, bangumi_subject_name:nb.name, bangumi_ep_id:null, newPath: buildNewPath(ep,newKey,nb.name,ep.episode_number,data.tvshow.title)};
          data.episodes[f] = {...data.episodes[f], season_number:newKey, bangumi_subject_name:nb.name, bangumi_ep_id:null, newPath: next[f].newPath};
        }
      }
      return next;
    });
  }, [data]);

  const handleSheetSave = useCallback((filename: string, updated: Partial<EpisodeBlock>) => {
    const old = data.episodes[filename];
    const subject = updated.bangumi_subject_name ?? old.bangumi_subject_name;
    const epNum = updated.episode_number ?? old.episode_number;
    let sn: number;
    if (old.season_number === 0) sn = 0;
    else if (updated.bangumi_subject_name !== undefined && data.bangumi_data) {
      sn = findSeasonNumber(data.bangumi_data, updated.bangumi_subject_name) ?? old.season_number;
    } else sn = updated.season_number ?? old.season_number;
    const ts = String(updated.tmdb_season ?? old.tmdb_season);
    const epInfo = data.tmdb_data[ts]?.episodes?.[String(epNum)] || null;
    const np = buildNewPath(old, sn, subject, epNum, data.tvshow.title);
    setEpisodes(prev => ({...prev, [filename]: {...prev[filename], ...updated, season_number:sn, tmdb:epInfo, newPath:np}}));
    Object.assign(data.episodes[filename], updated, { season_number:sn, tmdb:epInfo, newPath:np });
  }, [data]);

  const seasonOptions = Object.entries(data.tmdb_data).sort((a,b)=>parseInt(a[0])-parseInt(b[0]));

  return (
    <>
      {/* Header row */}
      <div className="flex items-center justify-between mb-4">
        <h4 className="text-base font-semibold text-foreground flex items-center gap-2">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-secondary">
            <line x1="8" y1="6" x2="21" y2="6" /><line x1="8" y1="12" x2="21" y2="12" /><line x1="8" y1="18" x2="21" y2="18" /><line x1="3" y1="6" x2="3.01" y2="6" /><line x1="3" y1="12" x2="3.01" y2="12" /><line x1="3" y1="18" x2="3.01" y2="18" />
          </svg>
          Unprocessed Files ({entries.length})
        </h4>
        <span className="px-2 py-0.5 bg-secondary/15 text-secondary text-[10px] font-bold rounded-full border border-secondary/20">
          Auto-Detect ON
        </span>
      </div>

      {/* Bento grid */}
      <div className="grid grid-cols-12 gap-6">
        {/* Cards — 8 cols */}
        <div className="col-span-12 lg:col-span-8 space-y-3">
          {entries.length === 0 ? (
            <p className="text-center py-12 text-muted-foreground text-sm">No files to map</p>
          ) : (
            entries.map(([filename, ep]) => {
              const hue = ((ep.bangumi_subject_name || filename).charCodeAt(0) || 1) * 137 % 360;
              const rate = !ep.tmdb ? 'none' as const : !ep.bangumi_ep_id ? 'partial' as const : 'matched' as const;
              return (
                <EpisodeCard
                  key={filename}
                  filename={filename}
                  ep={ep}
                  hue={hue}
                  matchRate={rate}
                  onEdit={setEditFilename}
                  subtitles={combinedSubtitles}
                  torrentName={data.torrent_name}
                  onSubtitleUploaded={handleSubtitleUploaded}
                />
              );
            })
          )}
        </div>

        {/* Stats — 4 cols */}
        <div className="col-span-12 lg:col-span-4">
          <StatsSidebar
            total={entries.length}
            matched={matched}
            partial={partial}
            unmatched={unmatched}
            title={data.tvshow.title}
          />
        </div>
      </div>

      {/* Edit Sheet */}
      {editFilename && episodes[editFilename] && (
        <EpisodeEditSheet
          open={!!editFilename}
          onOpenChange={(o) => { if (!o) setEditFilename(null); }}
          filename={editFilename}
          episode={episodes[editFilename]}
          seasonOptions={seasonOptions}
          bangumiData={data.bangumi_data || {}}
          onSave={handleSheetSave}
        />
      )}
    </>
  );
}
