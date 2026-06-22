import { useState, useEffect } from 'react';
import type { AppConfig } from '@/types/preview';
import * as rssApi from '@/api/rssApi';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Slider } from '@/components/ui/slider';

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

interface Props {
  config: AppConfig;
  onChange: (key: keyof AppConfig, value: string) => void;
}

export default function RssToolsPanel({ config, onChange }: Props) {
  const [dataStatus, setDataStatus] = useState<{ exists: boolean; count: number } | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [downloadMsg, setDownloadMsg] = useState('');

  const [dlRunning, setDlRunning] = useState(false);
  const [dlStatus, setDlStatus] = useState({ downloaded: 0, last_run: '', errors: [] as string[] });
  const [dlInterval, setDlInterval] = useState(30);

  const [excludePatterns, setExcludePatterns] = useState<string[]>(['全集']);
  const [excludeInput, setExcludeInput] = useState('');

  useEffect(() => {
    refreshDlStatus();
    checkDataStatus();
    rssApi.getRssSettings().then(s => setExcludePatterns(s.exclude_patterns)).catch(() => {});
  }, []);

  const checkDataStatus = async () => {
    try { setDataStatus(await rssApi.getDataStatus()); } catch { setDataStatus({ exists: false, count: 0 }); }
  };
  const handleDownloadData = async () => {
    setDownloading(true); setDownloadMsg('');
    try { const r = await rssApi.downloadData(); setDownloadMsg(r.output || 'Done.'); await checkDataStatus(); }
    catch (e: unknown) { setDownloadMsg(e instanceof Error ? e.message : 'Failed'); }
    finally { setDownloading(false); }
  };

  const refreshDlStatus = async () => {
    try {
      const [s, c] = await Promise.all([rssApi.getDownloaderStatus(), rssApi.getDownloaderConfig()]);
      setDlRunning(s.running); setDlStatus(s); setDlInterval(c.poll_interval_min);
    } catch { /* */ }
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

  return (
    <div className="flex flex-col gap-6">
      {/* ── RSS Mapping Data ── */}
      <SectionCard icon={<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-primary"><circle cx="12" cy="12" r="10"/><path d="M8.56 2.75c4.37 6.03 6.02 9.42 8.03 17.72m2.54-15.38c-3.72 4.35-8.94 5.66-13.88 5.66"/></svg>} title="RSS Mapping Data">
        <p className="text-xs text-muted-foreground mb-3">Query subtitle group RSS feeds on Mikan. Requires mapping data download first.</p>
        <div className="flex items-center justify-between">
          <span className="text-sm text-muted-foreground">
            {dataStatus === null ? 'Checking...'
              : dataStatus.exists ? `Ready (${dataStatus.count} entries)`
              : 'Not downloaded'}
          </span>
          <Button variant="secondary" size="sm" className="text-xs h-8"
            onClick={dataStatus?.exists ? checkDataStatus : handleDownloadData}
            disabled={downloading}>
            {downloading ? 'Downloading...' : dataStatus?.exists ? 'Refresh' : 'Download Data'}
          </Button>
        </div>
        {downloadMsg && <pre className="mt-3 p-3 bg-muted rounded-lg text-xs text-muted-foreground whitespace-pre-wrap max-h-24 overflow-y-auto">{downloadMsg}</pre>}
      </SectionCard>

      {/* ── RSS Download Path ── */}
      <SectionCard icon={<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-primary"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>} title="RSS Download Path">
        <p className="text-xs text-muted-foreground mb-3">Base directory for auto-downloaded files and generated NFO metadata.</p>
        <Input
          type="text"
          value={config.RSS_DOWNLOAD_PATH as string}
          placeholder="/Media/番剧"
          onChange={e => onChange('RSS_DOWNLOAD_PATH', e.target.value)}
        />
      </SectionCard>

      {/* ── Poller ── */}
      <SectionCard icon={<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-primary"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>} title="RSS Poller">
        <div className="flex items-center justify-between mb-4">
          <div>
            <p className="text-sm text-muted-foreground">
              {dlRunning ? '● Running' : '○ Stopped'}
              {dlStatus.last_run && ` · Last run ${dlStatus.last_run.slice(11, 16)}`}
            </p>
            {dlStatus.downloaded > 0 && (
              <p className="text-xs text-muted-foreground mt-0.5">Total downloaded: {dlStatus.downloaded} episodes</p>
            )}
          </div>
          <Switch checked={dlRunning} onCheckedChange={async (v) => {
            if (v) { await rssApi.setDownloaderInterval(dlInterval); await rssApi.startDownloader(); }
            else { await rssApi.stopDownloader(); }
            await refreshDlStatus();
          }} />
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-muted-foreground shrink-0">Interval</span>
          <Slider
            value={dlInterval} min={0} max={1440} step={1}
            onValueChange={setDlInterval}
            onValueCommit={async (v) => {
              await rssApi.setDownloaderInterval(v);
              await refreshDlStatus();
              toast.success(`Poll interval updated to ${v >= 60 ? `${(v/60).toFixed(1)}h` : `${v}m`}`, { duration: 3000, position: 'bottom-left' });
            }}
          />
          <span className="text-xs font-medium tabular-nums w-16 text-right">
            {dlInterval >= 60 ? `${(dlInterval/60).toFixed(1)}h` : `${dlInterval}m`}
          </span>
        </div>
        {dlStatus.errors.length > 0 && (
          <pre className="mt-3 p-3 bg-destructive/10 rounded-lg text-xs text-destructive whitespace-pre-wrap max-h-24 overflow-y-auto">{dlStatus.errors.join('\n')}</pre>
        )}
      </SectionCard>

      {/* ── Exclude Patterns ── */}
      <SectionCard icon={<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-primary"><circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/></svg>} title="Global Exclude Patterns">
        <p className="text-xs text-muted-foreground mb-3">Torrent titles matching these keywords will be skipped during RSS polling.</p>
        <div className="flex flex-wrap items-center gap-1.5">
          {excludePatterns.map(p => (
            <span key={p} className="text-xs bg-destructive/10 text-destructive px-2 py-1 rounded-full flex items-center gap-1">
              {p}
              <button className="hover:text-destructive/70 cursor-pointer text-xs font-bold" onClick={() => removeExclude(p)}>×</button>
            </span>
          ))}
          <input
            className="text-xs bg-muted border border-border rounded-full px-3 py-1 w-28 text-foreground placeholder:text-muted-foreground outline-none focus:border-primary"
            placeholder="Add keyword..."
            value={excludeInput}
            onChange={e => setExcludeInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') addExclude(); }}
          />
          <Button variant="outline" size="sm" className="text-xs h-7 rounded-full" onClick={addExclude}>+</Button>
        </div>
      </SectionCard>
    </div>
  );
}
