import type { AppConfig } from '@/types/preview';
import { cn } from '@/lib/utils';

function FieldRow({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-xs font-semibold text-muted-foreground mb-1.5">{label}</label>
      {children}
      {hint && <p className="text-[11px] text-muted-foreground/70 mt-1">{hint}</p>}
    </div>
  );
}

interface Props {
  config: AppConfig;
  dirty: Partial<AppConfig>;
  onChange: (key: keyof AppConfig, value: string) => void;
}

export default function TorrentConfigForm({ config, dirty, onChange }: Props) {
  const fieldClass = (key: keyof AppConfig) => cn(
    "w-full p-3 rounded-lg border bg-background focus:border-primary focus:ring-1 focus:ring-primary/20 transition-all outline-none text-sm",
    key in dirty
      ? "border-yellow-500/40 ring-1 ring-yellow-500/20"
      : "border-border",
  );

  const val = (k: keyof AppConfig) => {
    const v = config[k];
    return typeof v === 'number' ? String(v) : (v as string);
  };

  return (
    <div className="flex flex-col gap-6">
      <section className="bg-card rounded-xl sakura-shadow border border-border/30 p-6">
        <h3 className="text-base font-semibold mb-4">ASS 字体处理</h3>
        <div className="grid grid-cols-1 gap-5">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={config.FONTINASS_ENABLED}
              onChange={e => onChange('FONTINASS_ENABLED', String(e.target.checked))} />
            启用 FontInAss
          </label>
          <p className="text-xs text-muted-foreground">Torrent 下载完成并复制字幕后，自动处理 ASS 字体并替换目标字幕。SRT 不处理。处理失败时保留原字幕。开启后会将 ASS 字幕上传到配置的服务。</p>
          <FieldRow label="服务地址">
            <input className={fieldClass('FONTINASS_URL')} type="url" value={config.FONTINASS_URL}
              placeholder="https://font.anibt.net" onChange={e => onChange('FONTINASS_URL', e.target.value)} />
          </FieldRow>
          <FieldRow label="请求超时（秒）" hint="10–600 秒，默认 180 秒">
            <input className={fieldClass('FONTINASS_TIMEOUT')} type="number" min={10} max={600}
              value={config.FONTINASS_TIMEOUT} onChange={e => onChange('FONTINASS_TIMEOUT', e.target.value)} />
          </FieldRow>
        </div>
      </section>
      {/* ── Download Path ── */}
      <section className="bg-card rounded-xl sakura-shadow border border-border/30 p-6">
        <div className="flex items-center gap-2 mb-5">
          <span className="text-lg">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-primary">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="7 10 12 15 17 10" />
              <line x1="12" y1="15" x2="12" y2="3" />
            </svg>
          </span>
          <h3 className="text-base font-semibold text-foreground">Torrent Download</h3>
        </div>
        <div className="grid grid-cols-1 gap-5">
          <FieldRow label="Download Path" hint="Directory where torrent files are saved for watch/scan processing">
            <input
              className={fieldClass('TORRENT_DOWNLOAD_PATH')}
              type="text"
              value={val('TORRENT_DOWNLOAD_PATH')}
              placeholder="/data/downloads"
              onChange={e => onChange('TORRENT_DOWNLOAD_PATH', e.target.value)}
            />
          </FieldRow>
        </div>
      </section>

      {/* ── Hardlink Path ── */}
      <section className="bg-card rounded-xl sakura-shadow border border-border/30 p-6">
        <div className="flex items-center gap-2 mb-5">
          <span className="text-lg">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-primary">
              <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
              <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
            </svg>
          </span>
          <h3 className="text-base font-semibold text-foreground">Hardlink Path</h3>
        </div>
        <div className="grid grid-cols-1 gap-5">
          <FieldRow label="Hardlink Path" hint="Target directory for creating hard links of processed media files (Jellyfin library path)">
            <input
              className={fieldClass('TORRENT_HARDLINK_PATH')}
              type="text"
              value={val('TORRENT_HARDLINK_PATH')}
              placeholder="/Media/BD"
              onChange={e => onChange('TORRENT_HARDLINK_PATH', e.target.value)}
            />
          </FieldRow>
        </div>
      </section>

      {/* ── Movie Hardlink Path ── */}
      <section className="bg-card rounded-xl sakura-shadow border border-border/30 p-6">
        <div className="flex items-center gap-2 mb-5">
          <span className="text-lg">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-primary">
              <rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18" />
              <line x1="7" y1="2" x2="7" y2="22" />
              <line x1="17" y1="2" x2="17" y2="22" />
              <line x1="2" y1="12" x2="22" y2="12" />
              <line x1="2" y1="7" x2="7" y2="7" />
              <line x1="2" y1="17" x2="7" y2="17" />
              <line x1="17" y1="7" x2="22" y2="7" />
              <line x1="17" y1="17" x2="22" y2="17" />
            </svg>
          </span>
          <h3 className="text-base font-semibold text-foreground">Movie Hardlink Path</h3>
        </div>
        <div className="grid grid-cols-1 gap-5">
          <FieldRow label="Movie Hardlink Path" hint="Target directory for creating hard links of movie files (Jellyfin movie library path)">
            <input
              className={fieldClass('MOVIE_HARDLINK_PATH')}
              type="text"
              value={val('MOVIE_HARDLINK_PATH')}
              placeholder="/Media/剧场版"
              onChange={e => onChange('MOVIE_HARDLINK_PATH', e.target.value)}
            />
          </FieldRow>
        </div>
      </section>

      {/* ── Exclude Patterns ── */}
      <section className="bg-card rounded-xl sakura-shadow border border-border/30 p-6">
        <div className="flex items-center gap-2 mb-5">
          <span className="text-lg">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-primary">
              <circle cx="11" cy="11" r="8" />
              <line x1="21" y1="21" x2="16.65" y2="16.65" />
              <line x1="8" y1="11" x2="14" y2="11" />
            </svg>
          </span>
          <h3 className="text-base font-semibold text-foreground">Exclude Patterns</h3>
        </div>
        <div className="grid grid-cols-1 gap-5">
          <FieldRow label="Exclude Patterns" hint="Comma-separated keywords — torrents matching any pattern are skipped during watch/scan (e.g. hevc,10bit,av1)">
            <input
              className={fieldClass('TORRENT_EXCLUDE_PATTERNS')}
              type="text"
              value={val('TORRENT_EXCLUDE_PATTERNS')}
              placeholder="hevc,10bit,av1"
              onChange={e => onChange('TORRENT_EXCLUDE_PATTERNS', e.target.value)}
            />
          </FieldRow>
        </div>
      </section>
    </div>
  );
}
