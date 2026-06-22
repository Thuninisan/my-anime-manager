import { useState } from 'react';
import type { AppConfig } from '@/types/preview';
import FieldGroup from '@/components/shared/FieldGroup';
import { Button } from '@/components/ui/button';
import * as rssApi from '@/api/rssApi';

interface FieldDef { key: keyof AppConfig; label: string; placeholder: string; type: 'text' | 'number' | 'password'; hint: string; }

const FIELDS: FieldDef[] = [
  { key: 'QBITTORRENT_URL', label: 'qBittorrent URL', placeholder: 'http://localhost:8080', type: 'text', hint: 'qBittorrent WebUI address' },
  { key: 'QBITTORRENT_USERNAME', label: 'qBittorrent Username', placeholder: 'admin', type: 'text', hint: 'qBittorrent WebUI login username' },
  { key: 'QBITTORRENT_PASSWORD', label: 'qBittorrent Password', placeholder: 'password', type: 'password', hint: 'qBittorrent WebUI login password' },
  { key: 'QBITTORRENT_SAVE_PATH', label: 'qBittorrent Save Path', placeholder: '/downloads', type: 'text', hint: 'Download save path on the qBittorrent host' },
];

interface Props {
  config: AppConfig;
  dirty: Partial<AppConfig>;
  onChange: (key: keyof AppConfig, value: string) => void;
}

export default function QbitConfigForm({ config, dirty, onChange }: Props) {
  const [qbitStatus, setQbitStatus] = useState<{ ok: boolean; version: string; error: string } | null>(null);

  const checkConnection = async () => {
    try { setQbitStatus(await rssApi.checkQbit()); } catch { /* */ }
  };

  return (
    <div className="space-y-4">
      {FIELDS.map(f => {
        const val = config[f.key];
        const display = typeof val === 'number' ? String(val) : (val as string);
        const masked = display === '***';
        return (
          <FieldGroup
            key={f.key}
            id={`cfg-${f.key}`}
            label={f.label}
            hint={f.hint}
            type={f.type}
            value={masked ? '' : display}
            placeholder={masked ? '••• (unchanged)' : f.placeholder}
            dirty={f.key in dirty}
            onChange={v => onChange(f.key, v)}
          />
        );
      })}

      {/* Connection check */}
      <div className="border-t border-border pt-4">
        <p className="text-sm font-medium mb-2">连接检测</p>
        {qbitStatus === null ? (
          <div className="flex items-center justify-between gap-3">
            <span className="text-xs text-muted-foreground">未检测</span>
            <Button variant="secondary" size="sm" className="text-xs h-7" onClick={checkConnection}>检测连接</Button>
          </div>
        ) : qbitStatus.ok ? (
          <div className="flex items-center justify-between gap-3">
            <span className="text-xs text-muted-foreground">● 已连接 <span className="text-muted-foreground/70 ml-1">v{qbitStatus.version}</span></span>
            <Button variant="outline" size="sm" className="text-xs h-7" onClick={checkConnection}>重新检测</Button>
          </div>
        ) : (
          <div>
            <div className="flex items-center justify-between gap-3">
              <span className="text-xs text-destructive">● 连接失败</span>
              <Button variant="outline" size="sm" className="text-xs h-7" onClick={checkConnection}>重试</Button>
            </div>
            {qbitStatus.error && <p className="mt-1.5 text-xs text-destructive/70">{qbitStatus.error}</p>}
          </div>
        )}
      </div>
    </div>
  );
}
