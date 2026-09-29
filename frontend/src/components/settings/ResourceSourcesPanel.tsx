import { useEffect, useRef, useState, type FormEvent } from 'react';
import { addResourceSource, collectionStatus, collectNow, listResourceSources, setResourcePollInterval, type CollectionStatus, type ResourceSource } from '@/api/resourcesApi';
import { Button } from '@/components/ui/button';

const inputClass = 'w-full rounded-lg border border-border bg-background p-3 text-sm outline-none focus:border-primary';

export default function ResourceSourcesPanel() {
  const [sources, setSources] = useState<ResourceSource[]>([]);
  const [name, setName] = useState('');
  const [rssUrl, setRssUrl] = useState('');
  const [tag, setTag] = useState('enclosure');
  const [attribute, setAttribute] = useState('url');
  const [indexType, setIndexType] = useState<ResourceSource['index_type']>('tmdb');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [pollStatus, setPollStatus] = useState<CollectionStatus | null>(null);
  const [interval, setIntervalMinutes] = useState(1440);
  const [pollBusy, setPollBusy] = useState(false);
  const intervalLoaded = useRef(false);

  useEffect(() => {
    let active = true;
    listResourceSources()
      .then((items) => { if (active) setSources(items); })
      .catch((cause) => { if (active) setError(cause instanceof Error ? cause.message : '无法加载来源'); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    let active = true;
    const refresh = () => collectionStatus().then((status) => {
      if (active) {
        setPollStatus(status);
        if (!intervalLoaded.current) { setIntervalMinutes(status.poll_interval_min); intervalLoaded.current = true; }
      }
    }).catch((cause) => { if (active) setError(cause instanceof Error ? cause.message : '无法加载轮询状态'); });
    void refresh();
    const timer = window.setInterval(() => { void refresh(); }, 5000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  async function saveInterval() {
    setPollBusy(true); setError('');
    try {
      const status = await setResourcePollInterval(interval);
      setPollStatus(status); setIntervalMinutes(status.poll_interval_min);
      setMessage('资源轮询间隔已保存');
    } catch (cause) { setError(cause instanceof Error ? cause.message : '保存间隔失败'); }
    finally { setPollBusy(false); }
  }

  async function runNow() {
    setPollBusy(true); setError('');
    try { setPollStatus(await collectNow()); setMessage('已触发资源采集与识别'); }
    catch (cause) { setError(cause instanceof Error ? cause.message : '触发采集失败'); }
    finally { setPollBusy(false); }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError('');
    setMessage('');
    setSaving(true);
    try {
      const source = await addResourceSource({
        name: name.trim(), rss_url: rssUrl.trim(),
        downloadtag: { tag: tag.trim(), attribute: attribute.trim() || null },
        index_type: indexType,
      });
      setSources((current) => [...current.filter((item) => item.name !== source.name), source]);
      setName('');
      setRssUrl('');
      setMessage(`已添加来源 ${source.name}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '添加来源失败');
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="rounded-xl border border-border/30 bg-card p-6 sakura-shadow">
      <h3 className="mb-1 text-base font-semibold">Resource Sources</h3>
      <p className="mb-5 text-sm text-muted-foreground">添加 RSS 来源后，可通过资源采集接口获取种子。保存时会验证 feed 和种子链接。</p>
      {error && <p role="alert" className="mb-4 rounded-lg bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}
      {message && <p role="status" className="mb-4 rounded-lg bg-success/10 p-3 text-sm text-success">{message}</p>}
      <div className="mb-6 rounded-lg border border-border p-4">
        <h4 className="mb-2 text-sm font-semibold">资源轮询器</h4>
        <p className="mb-3 text-sm text-muted-foreground">{pollStatus?.running ? '已启用' : '未运行'}{pollStatus?.polling ? ' · 正在采集和识别' : ''} · 默认每 24 小时运行一次</p>
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-sm">间隔（分钟）
            <input className={`${inputClass} mt-1 w-32`} type="number" min={1} max={1440} value={interval} onChange={(event) => setIntervalMinutes(Number(event.target.value))} />
          </label>
          <Button type="button" variant="secondary" disabled={pollBusy || !pollStatus || interval < 1 || interval > 1440} onClick={() => void saveInterval()}>保存间隔</Button>
          <Button type="button" variant="secondary" disabled={pollBusy || !pollStatus || pollStatus.polling} onClick={() => void runNow()}>立即运行</Button>
        </div>
        {pollStatus?.last_result.length ? <p className="mt-3 text-xs text-muted-foreground">上轮：{pollStatus.last_result.map((result) => `${result.source} ${result.complete}/${result.seen}`).join(' · ')} · 新识别 {pollStatus.recognized ?? 0}</p> : null}
        {pollStatus?.errors.length ? <p className="mt-2 text-xs text-destructive">{pollStatus.errors.join('；')}</p> : null}
      </div>
      <div className="mb-6">
        <h4 className="mb-2 text-sm font-semibold">已配置来源</h4>
        {loading ? <p className="text-sm text-muted-foreground">加载中…</p> : sources.length === 0 ?
          <p className="text-sm text-muted-foreground">暂无来源</p> :
          <ul className="space-y-2">{sources.map((source) => (
            <li key={source.name} className="rounded-lg border border-border p-3 text-sm">
              <strong>{source.name}</strong> <span className="text-muted-foreground">· {source.index_type.toUpperCase()}</span>
              <p className="break-all text-muted-foreground">{source.rss_url}</p>
              <p className="text-xs text-muted-foreground">种子标签：{source.downloadtag.tag}{source.downloadtag.attribute ? ` @${source.downloadtag.attribute}` : '（文本内容）'}</p>
            </li>
          ))}</ul>}
      </div>
      <form onSubmit={submit} className="space-y-4">
        <h4 className="text-sm font-semibold">添加来源</h4>
        <label className="block text-sm">来源标识
          <input className={`${inputClass} mt-1`} value={name} onChange={(event) => setName(event.target.value)} required pattern="[A-Za-z0-9][A-Za-z0-9_-]{0,63}" placeholder="my-source" />
        </label>
        <label className="block text-sm">RSS URL
          <input className={`${inputClass} mt-1`} type="url" value={rssUrl} onChange={(event) => setRssUrl(event.target.value)} required pattern="https://.*" placeholder="https://example.com/feed.xml" />
        </label>
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block text-sm">种子链接标签
            <input className={`${inputClass} mt-1`} value={tag} onChange={(event) => setTag(event.target.value)} required placeholder="enclosure" />
          </label>
          <label className="block text-sm">链接属性（留空则读取文本）
            <input className={`${inputClass} mt-1`} value={attribute} onChange={(event) => setAttribute(event.target.value)} placeholder="url" />
          </label>
        </div>
        <label className="block text-sm">索引来源
          <select className={`${inputClass} mt-1`} value={indexType} onChange={(event) => setIndexType(event.target.value as ResourceSource['index_type'])}>
            <option value="tmdb">TMDB</option><option value="tvdb">TVDB</option>
          </select>
        </label>
        <Button type="submit" disabled={saving}>{saving ? '验证并添加中…' : '验证并添加来源'}</Button>
      </form>
    </section>
  );
}
