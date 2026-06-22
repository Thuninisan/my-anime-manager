import { useState } from 'react';
import type { AppConfig } from '@/types/preview';
import { Switch } from '@/components/ui/switch';
import { cn } from '@/lib/utils';

/* ======== Section Card ======== */
function SectionCard({ icon, title, children }: { icon: React.ReactNode; title: string; children: React.ReactNode }) {
  return (
    <section className="bg-card rounded-xl sakura-shadow border border-border/30 p-6">
      <div className="flex items-center gap-2 mb-5">
        <span className="text-lg">{icon}</span>
        <h3 className="text-base font-semibold text-foreground">{title}</h3>
      </div>
      {children}
    </section>
  );
}

/* ======== Field ======== */
function FieldRow({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-xs font-semibold text-muted-foreground mb-1.5">{label}</label>
      {children}
      {hint && <p className="text-[11px] text-muted-foreground/70 mt-1">{hint}</p>}
    </div>
  );
}

/* ======== Main ======== */
interface Props {
  config: AppConfig;
  dirty: Partial<AppConfig>;
  onChange: (key: keyof AppConfig, value: string) => void;
}

export default function GeneralConfigForm({ config, dirty, onChange }: Props) {
  const [proxyEnabled, setProxyEnabled] = useState(true);

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
      {/* ── API & Metadata ── */}
      <SectionCard icon={<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-primary"><path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/></svg>} title="API &amp; Metadata">
        <div className="grid grid-cols-1 gap-5">
          <FieldRow label="TMDB API Key" hint="Used for fetching series artwork and descriptions.">
            <input
              className={fieldClass('TMDB_API_KEY')}
              type="password"
              value={val('TMDB_API_KEY') === '***' ? '' : val('TMDB_API_KEY')}
              placeholder={val('TMDB_API_KEY') === '***' ? '••••••••••••••••' : 'your TMDB API key'}
              onChange={e => onChange('TMDB_API_KEY', e.target.value)}
            />
          </FieldRow>
          <div className="grid grid-cols-2 gap-5">
            <FieldRow label="Bangumi User-Agent">
              <input
                className={fieldClass('BANGUMI_UA')}
                type="text"
                value={val('BANGUMI_UA')}
                onChange={e => onChange('BANGUMI_UA', e.target.value)}
              />
            </FieldRow>
            <FieldRow label="API Request Delay (ms)">
              <input
                className={fieldClass('API_DELAY_MS')}
                type="number"
                value={val('API_DELAY_MS')}
                onChange={e => onChange('API_DELAY_MS', e.target.value)}
              />
            </FieldRow>
          </div>
        </div>
      </SectionCard>

      {/* ── Proxy Settings ── */}
      <SectionCard icon={<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-primary"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>} title="Proxy Settings">
        <div className="flex items-center justify-between mb-5">
          <span className="text-xs text-muted-foreground">HTTP proxy for external API requests</span>
          <label className="flex items-center gap-3 cursor-pointer">
            <Switch checked={proxyEnabled} onCheckedChange={setProxyEnabled} />
            <span className="text-xs font-semibold text-muted-foreground">
              {proxyEnabled ? 'Enabled' : 'Disabled'}
            </span>
          </label>
        </div>
        <div className="grid grid-cols-12 gap-5">
          <div className="col-span-9">
            <FieldRow label="Proxy Host">
              <input
                className={cn(fieldClass('PROXY_HOST'), !proxyEnabled && 'opacity-50')}
                type="text"
                placeholder="127.0.0.1"
                value={val('PROXY_HOST')}
                disabled={!proxyEnabled}
                onChange={e => onChange('PROXY_HOST', e.target.value)}
              />
            </FieldRow>
          </div>
          <div className="col-span-3">
            <FieldRow label="Port">
              <input
                className={cn(fieldClass('PROXY_PORT'), !proxyEnabled && 'opacity-50')}
                type="text"
                placeholder="7890"
                value={val('PROXY_PORT')}
                disabled={!proxyEnabled}
                onChange={e => onChange('PROXY_PORT', e.target.value)}
              />
            </FieldRow>
          </div>
        </div>
      </SectionCard>

      {/* ── Directories & URLs ── */}
      <SectionCard icon={<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-primary"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>} title="Directories &amp; URLs">
        <div className="flex flex-col gap-5">
          <FieldRow label="Torrent Watch Directory">
            <div className="flex gap-2">
              <div className="flex-1 p-3 rounded-lg border border-border bg-muted/30 text-sm text-muted-foreground flex items-center gap-2">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-muted-foreground"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
                <span>{val('TORRENT_WATCH_DIR') || '/data/torrent'}</span>
              </div>
              <button className="px-4 py-2 bg-muted rounded-lg text-xs font-semibold text-muted-foreground hover:bg-muted-foreground/10 transition-colors cursor-pointer">
                Browse
              </button>
            </div>
          </FieldRow>
          <FieldRow label="Mikan Base URL">
            <input
              className={fieldClass('MIKAN_BASE_URL')}
              type="text"
              value={val('MIKAN_BASE_URL')}
              onChange={e => onChange('MIKAN_BASE_URL', e.target.value)}
            />
          </FieldRow>
        </div>
      </SectionCard>

      {/* ── Status bar ── */}
      <div className="bg-accent/10 p-5 rounded-xl border border-accent/20 flex items-center justify-between">
        <div className="flex items-center gap-4">
          <div className="w-10 h-10 rounded-full bg-accent flex items-center justify-center text-white">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
          </div>
          <div>
            <p className="font-semibold text-sm text-foreground">Configuration Status</p>
            <p className="text-xs text-muted-foreground">
              {Object.keys(dirty).length > 0
                ? `${Object.keys(dirty).length} unsaved change(s) pending`
                : 'All settings saved and up to date.'}
            </p>
          </div>
        </div>
        <button
          className="px-4 py-2 bg-accent text-white rounded-lg text-xs font-bold hover:opacity-90 transition-opacity cursor-pointer"
          onClick={() => {}} // placeholder
        >
          Reset Defaults
        </button>
      </div>
    </div>
  );
}
