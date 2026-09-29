import { useEffect, useMemo, useState } from 'react';
import { listBangumiResources, type BangumiResource } from '@/api/resourcesApi';
import { DialogRoot, DialogContent, DialogHeader, DialogTitle, DialogBody } from '@/components/ui/dialog';
import PosterCard from '@/components/shared/PosterCard';
import { Button } from '@/components/ui/button';
import { useNavigate } from 'react-router-dom';

function torrentTags(torrent: BangumiResource['torrents'][number]): string[] {
  return [torrent.video_codec, torrent.source].flatMap((value) =>
    (Array.isArray(value) ? value : [value]).filter((tag): tag is string => typeof tag === 'string' && tag.trim().length > 0));
}

export default function ResourcesPage() {
  const navigate = useNavigate();
  const [items, setItems] = useState<BangumiResource[]>([]);
  const [selected, setSelected] = useState<BangumiResource | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [sortOrder, setSortOrder] = useState<'desc' | 'asc'>('desc');
  const [selectedTag, setSelectedTag] = useState('');

  useEffect(() => {
    let active = true;
    listBangumiResources()
      .then((result) => { if (active) setItems(result); })
      .catch((cause) => { if (active) setError(cause instanceof Error ? cause.message : '无法加载资源'); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  const tags = useMemo(() => [...new Set(items.flatMap((item) => item.torrents.flatMap(torrentTags)))].sort((a, b) => a.localeCompare(b)), [items]);
  const visibleItems = useMemo(() => items.map((item) => ({
    ...item,
    torrents: item.torrents
      .filter((torrent) => !selectedTag || torrentTags(torrent).includes(selectedTag))
      .sort((a, b) => {
        const comparison = (Date.parse(a.published_at) || 0) - (Date.parse(b.published_at) || 0);
        return (sortOrder === 'asc' ? comparison : -comparison) || b.resource_id - a.resource_id;
      }),
  })).filter((item) => item.torrents.length > 0)
    .sort((a, b) => {
      const comparison = (Date.parse(a.torrents[0].published_at) || 0) - (Date.parse(b.torrents[0].published_at) || 0);
      return (sortOrder === 'asc' ? comparison : -comparison) || a.bangumi_id - b.bangumi_id;
    }), [items, selectedTag, sortOrder]);
  const selectedVisible = visibleItems.find((item) => item.bangumi_id === selected?.bangumi_id);

  return (
    <section className="space-y-5">
      <div className="rounded-xl bg-card p-5 shadow-sm">
        <h1 className="text-lg font-semibold">Resources</h1>
        <p className="text-sm text-muted-foreground">按 Bangumi 剧集汇总已识别的种子资源</p>
        <div className="mt-4 flex flex-wrap gap-3">
          <label className="flex items-center gap-2 text-sm">时间排序
            <select className="rounded-md border border-border bg-background px-3 py-2" value={sortOrder} onChange={(event) => setSortOrder(event.target.value as 'asc' | 'desc')}>
              <option value="desc">最新在前</option>
              <option value="asc">最早在前</option>
            </select>
          </label>
          <label className="flex items-center gap-2 text-sm">种子标签
            <select className="rounded-md border border-border bg-background px-3 py-2" value={selectedTag} onChange={(event) => setSelectedTag(event.target.value)}>
              <option value="">全部标签</option>
              {tags.map((tag) => <option key={tag} value={tag}>{tag}</option>)}
            </select>
          </label>
        </div>
      </div>
      {error && <p className="rounded-lg bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}
      {loading && <p className="text-sm text-muted-foreground">加载中…</p>}
      {!loading && !error && items.length === 0 && <p className="rounded-xl bg-card p-8 text-center text-sm text-muted-foreground">暂无已识别资源</p>}
      {!loading && !error && items.length > 0 && visibleItems.length === 0 && <p className="rounded-xl bg-card p-8 text-center text-sm text-muted-foreground">没有符合该标签的种子</p>}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
        {visibleItems.map((item) => {
          const tags = [...new Set(item.torrents.flatMap(torrentTags))];
          return (
            <PosterCard key={item.bangumi_id} bangumiId={item.bangumi_id} name={item.name}
              posterUrl={`/api/rss/bangumi/${item.bangumi_id}/poster`}
              subtitle={`Bangumi #${item.bangumi_id} · ${item.torrents.length} 个种子`}
              tags={tags} onClick={() => setSelected(item)} />
          );
        })}
      </div>
      <DialogRoot open={selected !== null} onOpenChange={(open) => { if (!open) setSelected(null); }}>
        <DialogContent>
          <DialogHeader><DialogTitle>{selected?.name}</DialogTitle></DialogHeader>
          <DialogBody>
            <p className="mb-3 text-sm text-muted-foreground">{selectedVisible?.torrents.length ?? 0} 个种子</p>
            <ul className="max-h-[60vh] space-y-2 overflow-y-auto">
              {selectedVisible?.torrents.map((torrent) => (
                <li key={torrent.resource_id} className="rounded-lg border border-border p-3 text-sm">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <p className="break-all font-medium">{torrent.name}</p>
                      <p className="mt-1 text-xs text-muted-foreground">{torrentTags(torrent).join(' · ')}</p>
                      {torrent.published_at && <p className="mt-1 text-xs text-muted-foreground">发布时间：{torrent.published_at}</p>}
                    </div>
                    <Button type="button" variant="outline" size="sm" onClick={() => navigate('/torrent', { state: { resourceId: torrent.resource_id } })}>
                      转到下载页面
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          </DialogBody>
        </DialogContent>
      </DialogRoot>
    </section>
  );
}
