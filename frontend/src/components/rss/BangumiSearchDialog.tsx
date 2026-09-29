import type { BangumiOnlineResult } from '@/api/rssApi';
import { DialogRoot, DialogContent, DialogHeader, DialogTitle, DialogBody, DialogClose } from '@/components/ui/dialog';

interface Props {
  query: string;
  results: BangumiOnlineResult[];
  loading: boolean;
  error: string;
  onClose: () => void;
  onSelect: (item: BangumiOnlineResult) => void;
}

export default function BangumiSearchDialog({ query, results, loading, error, onClose, onSelect }: Props) {
  return (
    <DialogRoot open onOpenChange={open => { if (!open) onClose(); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Bangumi 搜索：{query}</DialogTitle>
          <DialogClose className="text-sm text-muted-foreground hover:text-foreground">关闭</DialogClose>
        </DialogHeader>
        <DialogBody className="max-h-[60vh] overflow-y-auto">
          {loading && <p className="text-sm text-muted-foreground">正在搜索 Bangumi...</p>}
          {error && <p className="text-sm text-destructive">{error}</p>}
          {!loading && !error && results.length === 0 && <p className="text-sm text-muted-foreground">没有找到匹配条目</p>}
          <div className="space-y-2">
            {results.map(item => (
              <button key={item.bangumi_id} type="button" onClick={() => onSelect(item)}
                className="flex w-full items-center gap-3 rounded-lg border border-border p-3 text-left hover:bg-muted cursor-pointer">
                <span className="flex h-16 w-11 shrink-0 items-center justify-center overflow-hidden rounded bg-muted text-[10px] text-muted-foreground">
                  {item.poster_url ? <img src={item.poster_url} alt="" loading="lazy" className="h-full w-full object-cover" /> : '无封面'}
                </span>
                <span className="min-w-0">
                  <span className="block text-sm font-medium">{item.name}</span>
                  {item.name_original && item.name_original !== item.name && <span className="block text-xs text-muted-foreground">{item.name_original}</span>}
                  <span className="block text-xs text-muted-foreground">ID: {item.bangumi_id}{item.date ? ` · ${item.date}` : ''}</span>
                </span>
              </button>
            ))}
          </div>
        </DialogBody>
      </DialogContent>
    </DialogRoot>
  );
}
