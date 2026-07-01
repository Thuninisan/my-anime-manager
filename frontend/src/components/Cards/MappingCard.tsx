import { useRef, useState } from 'react';
import type { MatchRow, BgmEpisode } from '@/components/MatchTable';
import { uploadSubtitle } from '@/api/torrentApi';

export interface TmdbSeasonOption {
  value: string;
  label: string;
}

export interface TmdbEpOption {
  epNum: number;
  name: string;
  name_cn?: string;
}

// Allowed subtitle extensions and accept attribute
const ALLOWED_SUB_EXTENSIONS = ['.ass', '.ssa', '.srt', '.sub', '.idx', '.vtt', '.ttml', '.sbv', '.dfxp'];
const SUB_ACCEPT = '.ass,.ssa,.srt,.sub,.idx,.vtt,.ttml,.sbv,.dfxp';

interface MappingCardProps {
  row: MatchRow;
  rowIndex: number;
  variant: 'tv' | 'sp' | 'movie';
  hasSubtitle: boolean;
  // Subtitle upload
  torrentName?: string;
  onSubtitleUploaded?: (filename: string) => void;
  // Dropdown data
  bgmEntryOptions: { id: number; name: string }[];
  currentEps: BgmEpisode[];
  currentEntryId: number;
  // TMDB options (pre-computed by caller; omitted for movies)
  tmdbSeasonOptions?: TmdbSeasonOption[];
  tmdbSeasonValue?: string | number;
  tmdbEpOptions?: TmdbEpOption[];
  tmdbEpValue?: string | number;
  tmdbEpTitle?: string;
  // Handlers (ep/season handlers omitted for movies)
  onBgmEntryChange: (value: string) => void;
  onBgmEpChange?: (value: string) => void;
  onTmdbSeasonChange?: (value: string) => void;
  onTmdbEpChange?: (value: string) => void;
  onToggleMatched: () => void;
}

export default function MappingCard({
  row,
  rowIndex: i,
  variant,
  hasSubtitle,
  torrentName,
  onSubtitleUploaded,
  bgmEntryOptions,
  currentEps,
  currentEntryId,
  tmdbSeasonOptions,
  tmdbSeasonValue,
  tmdbEpOptions,
  tmdbEpValue,
  tmdbEpTitle,
  onBgmEntryChange,
  onBgmEpChange,
  onTmdbSeasonChange,
  onTmdbEpChange,
  onToggleMatched,
}: MappingCardProps) {
  const isSp = variant === 'sp';
  const isMovie = variant === 'movie';
  const borderClass = isSp
    ? 'border-amber-500/20 dark:border-amber-500/20'
    : 'border-border-light dark:border-border-dark';
  const iconColor = isSp ? 'text-amber-400' : 'text-slate-300';
  const seasonSelectClass = isSp ? 'max-w-[160px] truncate' : '';

  // Subtitle upload state
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const handleSubUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !torrentName || !onSubtitleUploaded) return;

    const fileExt = '.' + file.name.split('.').pop()?.toLowerCase();
    if (!ALLOWED_SUB_EXTENSIONS.includes(fileExt)) {
      setUploadError(`不支持的字幕格式: ${fileExt}`);
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

  const showUploadButton = !hasSubtitle && torrentName && onSubtitleUploaded;

  return (
    <div className={`bg-surface-light dark:bg-surface-dark border ${borderClass} rounded-xl overflow-hidden shadow-sm transition-all hover:shadow-md group`}>
      {/* Top row: file name + badges */}
      <div className="px-4 py-3 border-b border-slate-50 dark:border-white/5">
        <div className="flex items-center justify-between gap-4">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-3">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={`${iconColor} shrink-0`}>
                <path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z" />
                <polyline points="14 2 14 8 20 8" />
              </svg>
              <h4 className="font-mono text-xs font-bold text-slate-700 dark:text-slate-200 truncate">{row.file_name}</h4>
            </div>
          </div>
          {hasSubtitle && (
            <span className="bg-[#f09199]/10 text-[#f09199] text-[9px] px-2 py-0.5 rounded-full font-bold uppercase tracking-wider shrink-0">Sub</span>
          )}
          {showUploadButton && (
            <>
              <input
                ref={fileInputRef}
                type="file"
                accept={SUB_ACCEPT}
                className="hidden"
                onChange={handleSubUpload}
              />
              <button
                className="bg-[#f09199]/10 text-[#f09199] text-[9px] px-2 py-0.5 rounded-full font-bold uppercase tracking-wider hover:bg-[#f09199]/25 transition-colors cursor-pointer disabled:opacity-50 shrink-0"
                title="上传字幕文件 (.ass, .srt 等)"
                onClick={() => fileInputRef.current?.click()}
                disabled={uploading}
              >
                {uploading ? '...' : '+Sub'}
              </button>
            </>
          )}
        </div>
        {/* Subtitle upload error */}
        {uploadError && (
          <p className="text-xs text-destructive mt-1">{uploadError}</p>
        )}
      </div>
      {/* Bottom row: mapping controls */}
      <div className="px-4 py-2.5 bg-slate-50/30 dark:bg-white/[0.02] flex flex-wrap items-center gap-x-6 gap-y-2">
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-slate-400 font-bold uppercase tracking-tighter">BGM Entry</span>
          <select
            className="text-[11px] py-0.5 px-1 bg-transparent border-slate-200 dark:border-white/10 rounded font-medium max-w-[100px] truncate focus:ring-1 focus:ring-primary/30 cursor-pointer"
            value={currentEntryId || ''}
            onChange={(e) => onBgmEntryChange(e.target.value)}
          >
            {!currentEntryId && <option value="" disabled>-</option>}
            {bgmEntryOptions.map((opt) => (
              <option key={opt.id} value={opt.id}>{opt.name}</option>
            ))}
          </select>
          {!isMovie && (
            <div className="flex items-center gap-1 ml-1">
              <span className="text-[10px] text-slate-400 font-bold uppercase tracking-tighter">#</span>
              <span className="text-[11px] font-mono text-slate-500">{row.bgm_sort ?? '-'}</span>
            </div>
          )}
        </div>
        {!isMovie && (
          <div className="flex items-center gap-2">
            <span className="text-[10px] text-slate-400 font-bold uppercase tracking-tighter">BGM Name</span>
            <select
              className="text-[11px] py-0.5 px-1 bg-transparent border-slate-200 dark:border-white/10 rounded font-medium max-w-[220px] truncate focus:ring-1 focus:ring-primary/30 cursor-pointer"
              value={row.bgm_ep_id ?? ''}
              onChange={(e) => onBgmEpChange?.(e.target.value)}
              title={`${row.bgm_ep_name}${row.bgm_ep_name_cn ? ` / ${row.bgm_ep_name_cn}` : ''}`}
            >
              {currentEps.length === 0 && (
                <option value="" disabled>{row.bgm_ep_name || '-'}</option>
              )}
              {currentEps.map((ep) => (
                <option key={`${i}-bgm-${ep.id}`} value={ep.id}>
                  E{ep.sort} {ep.name}{ep.name_cn ? ` / ${ep.name_cn}` : ''}
                </option>
              ))}
            </select>
          </div>
        )}
        {!isMovie && (
          <div className="flex items-center gap-2">
            <span className="text-[10px] text-slate-400 font-bold uppercase tracking-tighter">TMDB S</span>
            <select
              className={`text-[11px] py-0.5 px-1 bg-transparent border-slate-200 dark:border-white/10 rounded font-medium focus:ring-1 focus:ring-primary/30 cursor-pointer ${seasonSelectClass}`}
              value={tmdbSeasonValue ?? ''}
              onChange={(e) => onTmdbSeasonChange?.(e.target.value)}
            >
              {tmdbSeasonValue == null && <option value="" disabled>-</option>}
              {(tmdbSeasonOptions || []).map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          </div>
        )}
        {isMovie ? (
          <div className="flex items-center gap-2">
            <span className="text-[10px] text-slate-400 font-bold uppercase tracking-tighter">TMDB</span>
            <span className="text-[11px] text-slate-500 max-w-[250px] truncate">{row.tmdb_ep_name}</span>
          </div>
        ) : (
          <div className="flex items-center gap-2">
            <span className="text-[10px] text-slate-400 font-bold uppercase tracking-tighter">TMDB Ep</span>
            <select
              className="text-[11px] py-0.5 px-1 bg-transparent border-slate-200 dark:border-white/10 rounded font-medium max-w-[220px] truncate focus:ring-1 focus:ring-primary/30 cursor-pointer"
              value={tmdbEpValue ?? ''}
              onChange={(e) => onTmdbEpChange?.(e.target.value)}
              title={tmdbEpTitle}
            >
              {(tmdbEpOptions || []).length === 0 && (
                <option value="" disabled>{tmdbEpTitle || '-'}</option>
              )}
              {(tmdbEpOptions || []).map((ep) => (
                <option key={`${i}-tmdb-${ep.epNum}`} value={ep.epNum}>
                  E{ep.epNum} {ep.name}{ep.name_cn ? ` / ${ep.name_cn}` : ''}
                </option>
              ))}
            </select>
          </div>
        )}
        <div className="ml-auto">
          <button
            className="cursor-pointer select-none transition-all hover:scale-105 active:scale-95"
            onClick={onToggleMatched}
            title="Click to toggle mapped/pending"
          >
            {row.matched ? (
              <span className="bg-primary/10 text-primary text-[9px] px-2 py-0.5 rounded-full font-bold uppercase tracking-wider">Mapped</span>
            ) : (
              <span className="bg-secondary/10 text-secondary text-[9px] px-2 py-0.5 rounded-full font-bold uppercase tracking-wider">Pending</span>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
