import type { RssFeedItem } from '@/types/preview';
import { TAG_COLORS } from './TagFilterPanel';

function formatSize(bytes: number) {
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(0)} MB`;
  return `${(bytes / 1e3).toFixed(0)} KB`;
}

function renderTags(tags: string[]) {
  return tags.map(t => (
    <span key={t} className={`text-xs px-1.5 py-0.5 rounded ${TAG_COLORS[t] || 'bg-muted text-muted-foreground'}`}>
      {t}
    </span>
  ));
}

/* ── v1/v2 dedup ──────────────────────────────────────────────────
   Group by (episode_number + sorted tags), keep newest pub_date.
   Older items in the same group are marked as outdated (v1). ────── */

type ItemWithIndex = RssFeedItem & { _idx: number };

function markOutdated(items: RssFeedItem[]): (RssFeedItem & { outdated: boolean })[] {
  // Group by episode_number + tags (sorted, joined)
  const groups = new Map<string, ItemWithIndex[]>();
  items.forEach((item, idx) => {
    const ep = item.episode_number || 0;
    if (!ep) return;
    const tagKey = [...item.tags].sort().join(',');
    const key = `${ep}|${tagKey}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push({ ...item, _idx: idx });
  });

  // Mark newest in each group, others as outdated
  const newestIdx = new Set<number>();
  for (const [, group] of groups) {
    if (group.length < 2) {
      newestIdx.add(group[0]._idx);
      continue;
    }
    let newest = group[0];
    for (const g of group) {
      if ((g.pub_date || '') > (newest.pub_date || '')) {
        newest = g;
      }
    }
    newestIdx.add(newest._idx);
  }

  return items.map((item, idx) => ({
    ...item,
    outdated: item.episode_number > 0 && !newestIdx.has(idx),
  }));
}

/* ── Component ──────────────────────────────────────────────────── */

interface Props {
  items: RssFeedItem[];
  selectedTags: string[];
}

export default function FeedPreview({ items, selectedTags }: Props) {
  if (items.length === 0) {
    return <p className="py-4 text-center text-muted-foreground text-sm">No items in this feed</p>;
  }

  const marked = markOutdated(items);

  return (
    <div className="bg-muted rounded-lg">
      {marked.map((item, i) => {
        const passed = selectedTags.length === 0 || selectedTags.every(t => item.tags.includes(t));
        const dim = !passed || item.outdated || item.downloaded;
        return (
          <div
            key={i}
            className={`flex flex-wrap items-start gap-2 px-4 py-2.5 text-sm border-b border-border last:border-b-0 ${dim ? 'opacity-40' : ''}`}
          >
            <span className={`flex-1 min-w-0 break-all ${dim ? 'text-muted-foreground' : 'text-foreground'}`}>
              {item.guid}
            </span>

            <span className="shrink-0 flex items-center gap-1.5 text-xs">
              {/* Excluded badge */}
              {item.excluded && <span className="text-destructive font-medium">Excluded</span>}

              {/* v1 outdated badge */}
              {item.outdated && (
                <span className="text-[10px] px-1 py-0.5 rounded bg-muted-foreground/20 text-muted-foreground font-medium">
                  v1
                </span>
              )}

              {renderTags(item.tags)}
              <span className="text-muted-foreground min-w-16 text-right tabular-nums">
                {formatSize(item.size_bytes)}
              </span>

              {/* Status icon */}
              {item.downloaded || item.outdated ? (
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-muted-foreground">
                  <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
                </svg>
              ) : passed ? (
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-emerald-500">
                  <polyline points="20 6 9 17 4 12" />
                </svg>
              ) : (
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-destructive">
                  <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
                </svg>
              )}
            </span>
          </div>
        );
      })}
    </div>
  );
}
