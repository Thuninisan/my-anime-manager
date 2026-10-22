import type { SearchProvider, SearchResultItem } from '@/api/searchApi';
import { DialogRoot, DialogContent, DialogHeader, DialogTitle, DialogBody, DialogClose } from '@/components/ui/dialog';

interface Props {
  provider: SearchProvider;
  mediaType?: 'tv' | 'movie';
  query: string;
  results: SearchResultItem[];
  loading: boolean;
  error: string;
  selecting?: boolean;
  onClose: () => void;
  onSelect: (item: SearchResultItem) => void;
}

export default function SearchResultDialog({ provider, mediaType, query, results, loading, error, selecting = false, onClose, onSelect }: Props) {
  const label = provider === 'bangumi' ? 'Bangumi' : provider.toUpperCase();
  return (
    <DialogRoot open onOpenChange={open => { if (!open && !selecting) onClose(); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{label}{provider === 'tmdb' && mediaType ? ` ${mediaType === 'movie' ? '电影' : '剧集'}` : ''} 搜索：{query}</DialogTitle>
          <DialogClose disabled={selecting} className="text-sm text-muted-foreground hover:text-foreground disabled:opacity-50">关闭</DialogClose>
        </DialogHeader>
        <DialogBody className="max-h-[60vh] overflow-y-auto">
          {loading && <p role="status" className="text-sm text-muted-foreground">正在搜索 {label}...</p>}
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          {selecting && <p role="status" className="text-sm text-muted-foreground">正在添加作品并加载集数目录...</p>}
          {!loading && !error && results.length === 0 && <p className="text-sm text-muted-foreground">没有找到匹配条目</p>}
          <div className="space-y-2">
            {results.map(item => (
              <button key={`${item.provider}:${item.provider_id}`} type="button" disabled={loading || selecting} onClick={() => onSelect(item)}
                className="flex w-full items-center gap-3 rounded-lg border border-border p-3 text-left hover:bg-muted cursor-pointer disabled:cursor-wait disabled:opacity-50">
                <span className="flex h-16 w-11 shrink-0 items-center justify-center overflow-hidden rounded bg-muted text-[10px] text-muted-foreground">
                  {item.poster_url ? <img src={item.poster_url} alt="" loading="lazy" className="h-full w-full object-cover" /> : '无封面'}
                </span>
                <span className="min-w-0">
                  <span className="block text-sm font-medium">{item.name}</span>
                  {item.name_original && item.name_original !== item.name && <span className="block text-xs text-muted-foreground">{item.name_original}</span>}
                  <span className="block text-xs text-muted-foreground">{label} ID: {item.provider_id}{item.date ? ` · ${item.date}` : ''}</span>
                </span>
              </button>
            ))}
          </div>
        </DialogBody>
      </DialogContent>
    </DialogRoot>
  );
}
