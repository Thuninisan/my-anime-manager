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
