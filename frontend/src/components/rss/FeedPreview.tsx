import type { RssFeedItem } from '@/types/preview';
import { TAG_COLORS } from './TagFilterPanel';

function formatSize(bytes: number) {
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(0)} MB`;
  return `${(bytes / 1e3).toFixed(0)} KB`;
}

export function renderTags(tags: string[]) {
  return tags.map(t => (
    <span key={t} className={`text-xs px-1.5 py-0.5 rounded ${TAG_COLORS[t] || 'bg-muted text-muted-foreground'}`}>
      {t}
    </span>
  ));
}

interface Props {
  items: RssFeedItem[];
  selectedTags: string[];
}

export default function FeedPreview({ items, selectedTags }: Props) {
  if (items.length === 0) {
    return <p className="py-4 text-center text-muted-foreground text-sm">该 RSS 暂无条目</p>;
  }

  return (
    <div className="bg-muted rounded-lg">
      {items.map((item, i) => {
        const passed = selectedTags.length === 0 || selectedTags.every(t => item.tags.includes(t));
        return (
          <div key={i} className={`flex flex-wrap items-start gap-2 px-4 py-2.5 text-sm border-b border-border last:border-b-0 ${!passed ? 'opacity-40' : ''}`}>
            <span className={`flex-1 min-w-0 break-all ${item.downloaded || item.excluded ? 'text-muted-foreground' : 'text-foreground'}`}>
              {item.guid}
            </span>
            <span className="shrink-0 flex items-center gap-1.5 text-xs">
              {item.excluded && <span className="text-destructive font-medium">排除</span>}
              {renderTags(item.tags)}
              <span className="text-muted-foreground min-w-16 text-right">{formatSize(item.size_bytes)}</span>
              {passed ? <span className="text-success">✅</span> : <span className="text-destructive">❌</span>}
              {item.downloaded ? ' ✅' : ''}
            </span>
          </div>
        );
      })}
    </div>
  );
}
