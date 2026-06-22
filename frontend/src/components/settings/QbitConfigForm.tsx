import { useState } from 'react';
import type { AppConfig } from '@/types/preview';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import * as rssApi from '@/api/rssApi';

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

export default function QbitConfigForm({ config, dirty, onChange }: Props) {
  const [qbitStatus, setQbitStatus] = useState<{ ok: boolean; version: string; error: string } | null>(null);

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

  const masked = (k: keyof AppConfig) => val(k) === '***';

  return (
    <div className="flex flex-col gap-6">
      <section className="bg-card rounded-xl sakura-shadow border border-border/30 p-6">
        <div className="flex items-center gap-2 mb-5">
          <span className="text-lg"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-primary"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></span>
          <h3 className="text-base font-semibold text-foreground">qBittorrent Connection</h3>
        </div>
        <div className="grid grid-cols-2 gap-5">
          <FieldRow label="WebUI URL" hint="qBittorrent WebUI address">
            <input
              className={fieldClass('QBITTORRENT_URL')}
              type="text"
              value={val('QBITTORRENT_URL')}
              onChange={e => onChange('QBITTORRENT_URL', e.target.value)}
            />
          </FieldRow>
          <FieldRow label="Save Path" hint="Download save path on qBittorrent host">
            <input
              className={fieldClass('QBITTORRENT_SAVE_PATH')}
              type="text"
              value={val('QBITTORRENT_SAVE_PATH')}
              onChange={e => onChange('QBITTORRENT_SAVE_PATH', e.target.value)}
            />
          </FieldRow>
          <FieldRow label="Username">
            <input
              className={fieldClass('QBITTORRENT_USERNAME')}
              type="text"
              value={val('QBITTORRENT_USERNAME')}
              onChange={e => onChange('QBITTORRENT_USERNAME', e.target.value)}
            />
          </FieldRow>
          <FieldRow label="Password">
            <input
              className={fieldClass('QBITTORRENT_PASSWORD')}
              type="password"
              value={masked('QBITTORRENT_PASSWORD') ? '' : val('QBITTORRENT_PASSWORD')}
              placeholder={masked('QBITTORRENT_PASSWORD') ? '••••••••' : 'password'}
              onChange={e => onChange('QBITTORRENT_PASSWORD', e.target.value)}
            />
          </FieldRow>
        </div>
      </section>

      {/* Connection status */}
      <section className="bg-card rounded-xl sakura-shadow border border-border/30 p-6">
        <div className="flex items-center gap-2 mb-5">
          <span className="text-lg"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-primary"><path d="M18.36 6.64a9 9 0 1 1-12.73 0"/><line x1="12" y1="2" x2="12" y2="12"/></svg></span>
          <h3 className="text-base font-semibold text-foreground">Connection Test</h3>
        </div>
        {qbitStatus === null ? (
          <div className="flex items-center justify-between">
            <span className="text-sm text-muted-foreground">Not tested yet</span>
            <Button variant="secondary" size="sm" className="text-xs h-8"
              onClick={async () => { try { setQbitStatus(await rssApi.checkQbit()); } catch { /* */ } }}>
              Test Connection
            </Button>
          </div>
        ) : qbitStatus.ok ? (
          <div className="flex items-center justify-between">
            <span className="text-sm text-muted-foreground">
              <span className="text-success font-semibold">● Connected</span>
              <span className="ml-2 text-muted-foreground/60">v{qbitStatus.version}</span>
            </span>
            <Button variant="outline" size="sm" className="text-xs h-8"
              onClick={async () => { try { setQbitStatus(await rssApi.checkQbit()); } catch { /* */ } }}>
              Retest
            </Button>
          </div>
        ) : (
          <div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-destructive font-semibold">● Connection Failed</span>
              <Button variant="outline" size="sm" className="text-xs h-8"
                onClick={async () => { try { setQbitStatus(await rssApi.checkQbit()); } catch { /* */ } }}>
                Retry
              </Button>
            </div>
            {qbitStatus.error && <p className="mt-2 text-xs text-destructive/70">{qbitStatus.error}</p>}
          </div>
        )}
      </section>
    </div>
  );
}
