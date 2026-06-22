import { useState, useEffect } from 'react';
import type { AppConfig } from '@/types/preview';
import * as rssApi from '@/api/rssApi';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Slider } from '@/components/ui/slider';

interface Props {
  config: AppConfig;
  onChange: (key: keyof AppConfig, value: string) => void;
}

export default function RssToolsPanel({ config, onChange }: Props) {
  /* ── Data status ── */
  const [dataStatus, setDataStatus] = useState<{ exists: boolean; count: number } | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [downloadMsg, setDownloadMsg] = useState('');

  /* ── Poller ── */
  const [dlRunning, setDlRunning] = useState(false);
  const [dlStatus, setDlStatus] = useState({ downloaded: 0, last_run: '', errors: [] as string[] });
  const [dlInterval, setDlInterval] = useState(30);

  /* ── Exclude patterns ── */
  const [excludePatterns, setExcludePatterns] = useState<string[]>(['全集']);
  const [excludeInput, setExcludeInput] = useState('');

  /* ── Init ── */
  useEffect(() => {
    refreshDlStatus();
    checkDataStatus();
    rssApi.getRssSettings().then(s => setExcludePatterns(s.exclude_patterns)).catch(() => {});
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
      <div>
        <label htmlFor="cfg-RSS_DOWNLOAD_PATH" className="text-sm font-medium">RSS Download Path</label>
        <Input id="cfg-RSS_DOWNLOAD_PATH" type="text" value={config.RSS_DOWNLOAD_PATH as string}
          placeholder="/Media/番剧"
          onChange={e => onChange('RSS_DOWNLOAD_PATH', e.target.value)} className="mt-1" />
        <p className="text-xs text-muted-foreground mt-1">Base path for RSS auto-downloads (NFO + renamed files)</p>
      </div>

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
            value={dlInterval} min={0} max={1440} step={1}
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
  );
}
