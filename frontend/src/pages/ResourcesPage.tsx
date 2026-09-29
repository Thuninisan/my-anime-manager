import { useEffect, useState } from 'react';
import { listBangumiResources, type BangumiResource } from '@/api/resourcesApi';
import { DialogRoot, DialogContent, DialogHeader, DialogTitle, DialogBody } from '@/components/ui/dialog';
import PosterCard from '@/components/shared/PosterCard';

export default function ResourcesPage() {
  const [items, setItems] = useState<BangumiResource[]>([]);
  const [selected, setSelected] = useState<BangumiResource | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    listBangumiResources()
      .then((result) => { if (active) setItems(result); })
      .catch((cause) => { if (active) setError(cause instanceof Error ? cause.message : '无法加载资源'); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  return (
    <section className="space-y-5">
      <div className="rounded-xl bg-card p-5 shadow-sm">
        <h1 className="text-lg font-semibold">Resources</h1>
        <p className="text-sm text-muted-foreground">按 Bangumi 剧集汇总已识别的种子资源</p>
      </div>
      {error && <p className="rounded-lg bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}
      {loading && <p className="text-sm text-muted-foreground">加载中…</p>}
      {!loading && !error && items.length === 0 && <p className="rounded-xl bg-card p-8 text-center text-sm text-muted-foreground">暂无已识别资源</p>}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
        {items.map((item) => {
          const codecs = [...new Set(item.torrents.map((torrent) => torrent.video_codec).filter(Boolean))];
          const sources = [...new Set(item.torrents.map((torrent) => torrent.source).filter(Boolean))];
          return (
            <PosterCard key={item.bangumi_id} bangumiId={item.bangumi_id} name={item.name}
              posterUrl={`/api/rss/bangumi/${item.bangumi_id}/poster`}
              subtitle={`Bangumi #${item.bangumi_id} · ${item.torrents.length} 个种子`}
              tags={[...codecs, ...sources]} onClick={() => setSelected(item)} />
          );
        })}
      </div>
      <DialogRoot open={selected !== null} onOpenChange={(open) => { if (!open) setSelected(null); }}>
        <DialogContent>
          <DialogHeader><DialogTitle>{selected?.name}</DialogTitle></DialogHeader>
          <DialogBody>
            <p className="mb-3 text-sm text-muted-foreground">{selected?.torrents.length ?? 0} 个种子</p>
            <ul className="max-h-[60vh] space-y-2 overflow-y-auto">
              {selected?.torrents.map((torrent) => (
                <li key={torrent.resource_id} className="rounded-lg border border-border p-3 text-sm">
                  <p className="break-all font-medium">{torrent.name}</p>
                  <p className="mt-1 text-xs text-muted-foreground">{[torrent.video_codec, torrent.source].filter(Boolean).join(' · ')}</p>
                </li>
              ))}
            </ul>
          </DialogBody>
        </DialogContent>
      </DialogRoot>
    </section>
  );
}
