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
                className="w-full rounded-lg border border-border p-3 text-left hover:bg-muted cursor-pointer">
                <span className="block text-sm font-medium">{item.name}</span>
                {item.name_original && item.name_original !== item.name && <span className="block text-xs text-muted-foreground">{item.name_original}</span>}
                <span className="block text-xs text-muted-foreground">ID: {item.bangumi_id}{item.date ? ` · ${item.date}` : ''}</span>
              </button>
            ))}
          </div>
        </DialogBody>
      </DialogContent>
    </DialogRoot>
  );
}
