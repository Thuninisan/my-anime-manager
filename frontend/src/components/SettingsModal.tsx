import { useState, useEffect } from 'react';
import type { AppConfig } from '../types/preview';
import * as api from '../api/torrentApi';
import * as rssApi from '../api/rssApi';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';

interface Props { onClose: () => void; }

interface FieldDef { key: keyof AppConfig; label: string; placeholder: string; type: 'text' | 'number' | 'password'; hint: string; }

const FIELDS_CONFIG: FieldDef[] = [
  { key: 'TMDB_API_KEY', label: 'TMDB API Key', placeholder: 'your TMDB API key', type: 'password', hint: 'From https://www.themoviedb.org/settings/api' },
  { key: 'BANGUMI_UA', label: 'Bangumi User-Agent', placeholder: 'JellyfinTmdbHelper/1.0', type: 'text', hint: 'Custom User-Agent for Bangumi API requests' },
  { key: 'API_DELAY_MS', label: 'API Delay (ms)', placeholder: '600', type: 'number', hint: 'Delay between API calls to avoid rate limiting' },
  { key: 'PROXY_HOST', label: 'Proxy Host', placeholder: '127.0.0.1', type: 'text', hint: 'HTTP proxy host (leave empty to disable)' },
  { key: 'PROXY_PORT', label: 'Proxy Port', placeholder: '7890', type: 'number', hint: 'HTTP proxy port' },
  { key: 'TORRENT_WATCH_DIR', label: 'Torrent Watch Directory', placeholder: '/data/torrent', type: 'text', hint: 'Directory to watch for .torrent files (scan mode)' },
  { key: 'MIKAN_BASE_URL', label: 'Mikan Base URL', placeholder: 'https://mikanani.me', type: 'text', hint: 'Mikanani.me base URL' },
];

const FIELDS_QBIT: FieldDef[] = [
  { key: 'QBITTORRENT_URL', label: 'qBittorrent URL', placeholder: 'http://localhost:8080', type: 'text', hint: 'qBittorrent WebUI address' },
  { key: 'QBITTORRENT_USERNAME', label: 'qBittorrent Username', placeholder: 'admin', type: 'text', hint: 'qBittorrent WebUI login username' },
  { key: 'QBITTORRENT_PASSWORD', label: 'qBittorrent Password', placeholder: 'password', type: 'password', hint: 'qBittorrent WebUI login password' },
  { key: 'QBITTORRENT_SAVE_PATH', label: 'qBittorrent Save Path', placeholder: '/downloads', type: 'text', hint: 'Download save path on the qBittorrent host' },
];

/* ── Slider ── */
function Slider({ value, min, max, step, onValueChange, onValueCommit }: {
  value: number; min: number; max: number; step: number;
  onValueChange: (v: number) => void;
  onValueCommit?: (v: number) => void;
}) {
  return (
    <input type="range" min={min} max={max} step={step} value={value}
      onChange={e => onValueChange(parseInt(e.target.value, 10))}
      onMouseUp={e => onValueCommit?.(parseInt((e.target as HTMLInputElement).value, 10))}
      onTouchEnd={e => onValueCommit?.(parseInt((e.target as HTMLInputElement).value, 10))}
      className={cn(
        "w-full h-1.5 rounded-full appearance-none bg-muted cursor-pointer accent-primary",
        "[&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:size-4",
        "[&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-primary",
        "[&::-webkit-slider-thumb]:shadow-sm [&::-webkit-slider-thumb]:cursor-pointer",
      )}
    />
  );
}

/* ── Switch ── */
function Switch({ checked, onCheckedChange, disabled }: {
  checked: boolean; onCheckedChange: (v: boolean) => void; disabled?: boolean;
}) {
  return (
    <button role="switch" aria-checked={checked} disabled={disabled}
      onClick={() => onCheckedChange(!checked)}
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
        checked ? "bg-primary" : "bg-muted-foreground/30",
        disabled && "opacity-50 cursor-not-allowed",
      )}
    >
      <span className={cn(
        "pointer-events-none block size-4 rounded-full bg-background shadow-sm ring-0 transition-transform",
        checked ? "translate-x-4" : "translate-x-0",
      )} />
    </button>
  );
}

/* ── Sidebar nav item ── */
function SidebarNavItem({ label, active, onClick }: {
  label: string; active: boolean; onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "w-full px-3 py-2 rounded-md text-sm font-medium text-left transition-colors cursor-pointer",
        active
          ? "bg-sidebar-accent text-sidebar-accent-foreground"
          : "text-muted-foreground hover:text-foreground hover:bg-muted/50",
      )}
    >
      {label}
    </button>
  );
}

export default function SettingsModal({ onClose }: Props) {
  const [tab, setTab] = useState('config');
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [dirty, setDirty] = useState<Partial<AppConfig>>({});

  const [dlRunning, setDlRunning] = useState(false);
  const [dlStatus, setDlStatus] = useState({ downloaded: 0, last_run: '', errors: [] as string[] });
  const [dlInterval, setDlInterval] = useState(30);
  const [dataStatus, setDataStatus] = useState<{ exists: boolean; count: number } | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [downloadMsg, setDownloadMsg] = useState('');
  const [qbitStatus, setQbitStatus] = useState<{ ok: boolean; version: string; error: string } | null>(null);
  const [excludePatterns, setExcludePatterns] = useState<string[]>(['全集']);
  const [excludeInput, setExcludeInput] = useState('');
  useEffect(() => {
    api.getConfig().then(cfg => { setConfig(cfg); setLoading(false); })
      .catch(err => { setError(err instanceof Error ? err.message : 'Failed'); setLoading(false); });
  }, []);

  const checkDataStatus = async () => {
    try { setDataStatus(await rssApi.getDataStatus()); }
    catch { setDataStatus({ exists: false, count: 0 }); }
  };
  const handleDownloadData = async () => {
    setDownloading(true); setDownloadMsg('');
    try { const r = await rssApi.downloadData(); setDownloadMsg(r.output || 'Done.'); await checkDataStatus(); }
    catch (e: unknown) { setDownloadMsg(e instanceof Error ? e.message : 'Failed'); }
    finally { setDownloading(false); }
  };

  useEffect(() => {
    refreshDlStatus();
    checkDataStatus();
    rssApi.getRssSettings().then(s => setExcludePatterns(s.exclude_patterns)).catch(() => {});
  }, []);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  const refreshDlStatus = async () => {
    try {
      const [s, c] = await Promise.all([rssApi.getDownloaderStatus(), rssApi.getDownloaderConfig()]);
      setDlRunning(s.running); setDlStatus(s); setDlInterval(c.poll_interval_min);
    } catch { /* */ }
  };

  const handleChange = (key: keyof AppConfig, value: string) => {
    if (!config) return;
    const current = config[key];
    let newVal: string | number = value;
    if (typeof current === 'number') { newVal = value === '' ? 0 : parseInt(value, 10); if (isNaN(newVal as number)) return; }
    setDirty(prev => { const next = { ...prev }; if (newVal === current) delete next[key]; else (next as Record<string, unknown>)[key] = newVal; return next; });
    setConfig(prev => prev ? { ...prev, [key]: newVal } : prev);
  };

  const handleSave = async () => {
    if (!config || Object.keys(dirty).length === 0) return;
    setSaving(true); setError(null); setSaved(false);
    try {
      const updated = await api.updateConfig(dirty);
      setConfig(updated); setDirty({}); setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed');
    } finally { setSaving(false); }
  };

  const addExclude = async () => {
    const v = excludeInput.trim(); if (!v || excludePatterns.includes(v)) return;
    const next = [...excludePatterns, v]; setExcludePatterns(next); setExcludeInput('');
    try { await rssApi.updateRssSettings({ exclude_patterns: next }); } catch { /* */ }
  };
  const removeExclude = async (p: string) => {
    const next = excludePatterns.filter(x => x !== p); setExcludePatterns(next);
    try { await rssApi.updateRssSettings({ exclude_patterns: next }); } catch { /* */ }
  };
  const renderFields = (fields: FieldDef[]) => fields.map(f => {
    if (!config) return null;
    const value = config[f.key];
    const displayVal = typeof value === 'number' ? String(value) : (value as string);
    const isDirty = f.key in dirty;
    const isMasked = displayVal === '***';
    return (
      <div key={f.key} className={isDirty ? 'ring-1 ring-yellow-500/30 rounded-lg p-2 -mx-2' : ''}>
        <label htmlFor={`cfg-${f.key}`} className="text-sm font-medium flex items-center gap-1.5">
          {f.label} {isDirty && <span className="w-2 h-2 rounded-full bg-yellow-400" />}
        </label>
        <Input id={`cfg-${f.key}`} type={f.type} value={isMasked ? '' : displayVal}
          placeholder={isMasked ? '••• (unchanged)' : f.placeholder}
          onChange={e => handleChange(f.key, e.target.value)} className="mt-1" />
        <p className="text-xs text-muted-foreground mt-1">{f.hint}</p>
      </div>
    );
  });

  const hasChanges = Object.keys(dirty).length > 0;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-card rounded-xl ring-1 ring-border shadow-2xl w-full max-w-2xl max-h-[85vh] flex flex-col mx-4" onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-border">
          <h2 className="text-lg font-semibold">Settings</h2>
          <button className="text-muted-foreground hover:text-foreground text-xl w-8 h-8 flex items-center justify-center rounded cursor-pointer" onClick={onClose}>✕</button>
        </div>

        {/* Body: sidebar + content */}
        <div className="flex-1 min-h-0 flex">
          {loading ? (
            <div className="flex-1 flex flex-col items-center justify-center gap-3 py-8">
              <div className="w-8 h-8 border-4 border-primary/30 border-t-primary rounded-full animate-spin" />
              <p className="text-sm text-muted-foreground">Loading...</p>
            </div>
          ) : (
            <>
              {/* Sidebar */}
              <div className="shrink-0 w-36 border-r border-border flex flex-col gap-0.5 px-3 py-4 bg-muted/20">
                <SidebarNavItem label="配置" active={tab === 'config'} onClick={() => setTab('config')} />
                <SidebarNavItem label="qBittorrent" active={tab === 'qbit'} onClick={() => setTab('qbit')} />
                <SidebarNavItem label="RSS" active={tab === 'tools'} onClick={() => setTab('tools')} />
              </div>

              {/* Content */}
              <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
                {error && <div className="text-destructive text-sm p-3 bg-destructive/10 rounded-lg">{error}</div>}

                {tab === 'config' && renderFields(FIELDS_CONFIG)}

                {tab === 'qbit' && (
                  <div className="space-y-4">
                    {renderFields(FIELDS_QBIT)}
                    {/* Connection check */}
                    <div className="border-t border-border pt-4">
                      <p className="text-sm font-medium mb-2">连接检测</p>
                      {qbitStatus === null ? (
                        <div className="flex items-center justify-between gap-3">
                          <span className="text-xs text-muted-foreground">未检测</span>
                          <Button variant="secondary" size="sm" className="text-xs h-7" onClick={async () => {
                            try { setQbitStatus(await rssApi.checkQbit()); } catch { /* */ }
                          }}>检测连接</Button>
                        </div>
                      ) : qbitStatus.ok ? (
                        <div className="flex items-center justify-between gap-3">
                          <span className="text-xs text-muted-foreground">● 已连接 <span className="text-muted-foreground/70 ml-1">v{qbitStatus.version}</span></span>
                          <Button variant="outline" size="sm" className="text-xs h-7" onClick={async () => {
                            try { setQbitStatus(await rssApi.checkQbit()); } catch { /* */ }
                          }}>重新检测</Button>
                        </div>
                      ) : (
                        <div>
                          <div className="flex items-center justify-between gap-3">
                            <span className="text-xs text-destructive">● 连接失败</span>
                            <Button variant="outline" size="sm" className="text-xs h-7" onClick={async () => {
                              try { setQbitStatus(await rssApi.checkQbit()); } catch { /* */ }
                            }}>重试</Button>
                          </div>
                          {qbitStatus.error && <p className="mt-1.5 text-xs text-destructive/70">{qbitStatus.error}</p>}
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {tab === 'tools' && (
                  <div className="space-y-5">
                    {/* RSS 映射数据 */}
                    <div>
                      <p className="text-sm font-medium mb-2">RSS 映射数据</p>
                      <p className="text-xs text-muted-foreground mb-2">查询番剧在 Mikan 上的字幕组 RSS，需要先下载映射数据</p>
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-xs text-muted-foreground">
                          {dataStatus === null ? '检查中...' : dataStatus.exists ? `就绪 (${dataStatus.count} 条)` : '未下载'}
                        </span>
                        <Button variant="secondary" size="sm" className="text-xs h-7"
                          onClick={dataStatus?.exists ? checkDataStatus : handleDownloadData}
                          disabled={downloading}>
                          {downloading ? '下载中...' : dataStatus?.exists ? '刷新' : '下载数据'}
                        </Button>
                      </div>
                      {downloadMsg && <pre className="mt-2 p-2 bg-muted rounded text-xs text-muted-foreground whitespace-pre-wrap max-h-24 overflow-y-auto">{downloadMsg}</pre>}
                    </div>

                    <div className="border-t border-border" />

                    {/* RSS Download Path */}
                    {config && (() => {
                      const f = { key: 'RSS_DOWNLOAD_PATH' as keyof AppConfig, label: 'RSS Download Path', placeholder: '/Media/番剧', type: 'text' as const, hint: 'Base path for RSS auto-downloads (NFO + renamed files)' };
                      const value = config[f.key];
                      const displayVal = typeof value === 'number' ? String(value) : (value as string);
                      return (
                        <div>
                          <label htmlFor="cfg-RSS_DOWNLOAD_PATH" className="text-sm font-medium">RSS Download Path</label>
                          <Input id="cfg-RSS_DOWNLOAD_PATH" type="text" value={displayVal}
                            placeholder={f.placeholder}
                            onChange={e => handleChange(f.key, e.target.value)} className="mt-1" />
                          <p className="text-xs text-muted-foreground mt-1">{f.hint}</p>
                        </div>
                      );
                    })()}

                    <div className="border-t border-border" />

                    {/* RSS 轮询器 */}
                    <div>
                      <p className="text-sm font-medium mb-3">RSS 轮询器</p>
                      <div className="flex items-center justify-between mb-3">
                        <span className="text-xs text-muted-foreground">
                          {dlRunning ? '● 运行中' : '○ 已停止'}
                          {dlStatus.last_run && ` · 上次 ${dlStatus.last_run.slice(11, 16)}`}
                          {dlStatus.downloaded > 0 && ` · 累计 ${dlStatus.downloaded} 集`}
                        </span>
                        <Switch checked={dlRunning} onCheckedChange={async (v) => {
                          if (v) { await rssApi.setDownloaderInterval(dlInterval); await rssApi.startDownloader(); }
                          else { await rssApi.stopDownloader(); }
                          await refreshDlStatus();
                        }} />
                      </div>
                      <div className="flex items-center gap-3">
                        <span className="text-xs text-muted-foreground shrink-0">间隔</span>
                        <Slider
                          value={dlInterval}
                          min={0} max={1440} step={1}
                          onValueChange={setDlInterval}
                          onValueCommit={async (v) => {
                            await rssApi.setDownloaderInterval(v);
                            await refreshDlStatus();
                            toast.success(`轮询间隔已更新为 ${v >= 60 ? `${(v / 60).toFixed(1)} 小时` : `${v} 分钟`}`, { duration: 3000, position: 'bottom-left' });
                          }}
                        />
                        <span className="text-xs font-medium tabular-nums w-16 text-right">
                          {dlInterval >= 60 ? `${(dlInterval / 60).toFixed(1)}h` : `${dlInterval}m`}
                        </span>
                      </div>
                      {dlStatus.errors.length > 0 && (
                        <pre className="mt-2 p-2 bg-destructive/10 rounded text-xs text-destructive whitespace-pre-wrap max-h-24 overflow-y-auto">{dlStatus.errors.join('\n')}</pre>
                      )}
                    </div>

                    <div className="border-t border-border" />

                    {/* 全局排除 */}
                    <div>
                      <p className="text-sm font-medium mb-2">全局排除</p>
                      <div className="flex flex-wrap items-center gap-1.5">
                        {excludePatterns.map(p => (
                          <span key={p} className="text-xs bg-destructive/15 text-destructive px-1.5 py-0.5 rounded flex items-center gap-1">
                            {p}<button className="text-destructive hover:text-destructive/80 cursor-pointer text-xs" onClick={() => removeExclude(p)}>✕</button>
                          </span>
                        ))}
                        <input className="text-xs bg-muted border border-border rounded px-2 py-1 w-24 text-foreground placeholder:text-muted-foreground"
                          placeholder="新增..." value={excludeInput} onChange={e => setExcludeInput(e.target.value)}
                          onKeyDown={e => { if (e.key === 'Enter') addExclude(); }} />
                        <Button variant="outline" size="sm" className="text-xs h-6" onClick={addExclude}>+</Button>
                      </div>
                    </div>

                  </div>
                )}
              </div>
            </>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-6 py-4 border-t border-border">
          {saved ? <span className="text-sm text-success">Settings saved</span> : <span />}
          <div className="flex gap-2">
            <Button variant="outline" onClick={onClose}>Cancel</Button>
            {(tab === 'config' || tab === 'qbit') && (
              <Button onClick={handleSave} disabled={!hasChanges || saving}>{saving ? 'Saving...' : 'Save'}</Button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
