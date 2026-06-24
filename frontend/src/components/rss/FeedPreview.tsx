import { useState } from 'react';
import type { RssFeedItem } from '@/types/preview';
import { TAG_COLORS } from './TagFilterPanel';

const INITIAL_SHOW = 5;

function formatSize(bytes: number) {
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(0)} MB`;
  return `${(bytes / 1e3).toFixed(0)} KB`;
}

function renderTags(tags: string[]) {
  return tags.map(t => (
    <span key={t} className={`text-[10px] px-1.5 py-0.5 rounded font-bold ${TAG_COLORS[t] || 'bg-muted text-muted-foreground'}`}>
      {t}
    </span>
  ));
}

/* ── v1/v2 dedup ────────────────────────────────────────────────── */

type ItemWithIndex = RssFeedItem & { _idx: number };

function markOutdated(items: RssFeedItem[]): (RssFeedItem & { outdated: boolean })[] {
  const groups = new Map<string, ItemWithIndex[]>();
  items.forEach((item, idx) => {
    const ep = item.episode_number || 0;
    if (!ep) return;
    const tagKey = [...item.tags].sort().join(',');
    const key = `${ep}|${tagKey}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push({ ...item, _idx: idx });
  });
  const newestIdx = new Set<number>();
  for (const [, group] of groups) {
    if (group.length < 2) { newestIdx.add(group[0]._idx); continue; }
    let newest = group[0];
    for (const g of group) {
      if ((g.pub_date || '') > (newest.pub_date || '')) newest = g;
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
  excludeKeywords?: string[];
}

export default function FeedPreview({ items, selectedTags, excludeKeywords = [] }: Props) {
  const [showAll, setShowAll] = useState(false);

  if (items.length === 0) {
    return <p className="py-4 text-center text-muted-foreground text-sm">No items in this feed</p>;
  }

  const marked = markOutdated(items);
  const visible = showAll ? marked : marked.slice(0, INITIAL_SHOW);
  const hidden = marked.length - INITIAL_SHOW;

  return (
    <div>
      {/* Table */}
      <table className="w-full text-left border-collapse">
        <thead className="bg-muted/30">
          <tr className="text-[10px] uppercase tracking-widest text-muted-foreground border-b border-border">
            <th className="px-5 py-2 font-semibold">Title / Episode</th>
            <th className="px-4 py-2 font-semibold w-20">Size</th>
            <th className="px-4 py-2 font-semibold">Tags</th>
            <th className="px-5 py-2 font-semibold w-14 text-center">Status</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border/50">
          {visible.map((item, i) => {
            const hasEp = item.episode_number > 0;
            const kwMatch = excludeKeywords.length === 0 || !excludeKeywords.some(k => (item.guid || item.title).includes(k));
            const passed = !item.excluded && kwMatch && (selectedTags.length === 0 || selectedTags.every(t => item.tags.includes(t)));
            const dim = !passed || item.outdated || item.downloaded;

            return (
              <tr key={i} className={`hover:bg-muted/20 transition-colors ${dim ? 'opacity-50' : ''}`}>
                <td className="px-5 py-2.5">
                  <div className={`text-[13px] font-bold truncate max-w-lg ${dim ? 'text-muted-foreground' : 'text-foreground'}`} title={item.guid}>
                    {item.guid}
                  </div>
                  <div className="flex items-center gap-2 mt-0.5">
                    {hasEp && (
                      <span className={`text-[10px] font-bold ${dim ? 'text-muted-foreground/60' : 'text-primary'}`}>
                        EP{item.episode_number.toString().padStart(2, '0')}
                        {item.outdated ? ' (v1)' : ''}
                      </span>
                    )}
                    {item.excluded && (
                      <span className="text-[10px] text-destructive font-medium">Excluded</span>
                    )}
                    {item.outdated && (
                      <span className="text-[10px] px-1 py-0.5 rounded bg-muted-foreground/15 text-muted-foreground font-medium">
                        v1 skipped
                      </span>
                    )}
                  </div>
                </td>
                <td className="px-4 py-2.5 text-xs text-muted-foreground tabular-nums">
                  {formatSize(item.size_bytes)}
                </td>
                <td className="px-4 py-2.5">
                  <div className="flex flex-wrap gap-1">{renderTags(item.tags)}</div>
                </td>
                <td className="px-5 py-2.5 text-center">
                  {item.downloaded || item.outdated ? (
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-muted-foreground mx-auto">
                      <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
                    </svg>
                  ) : passed ? (
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-emerald-500 mx-auto">
                      <polyline points="20 6 9 17 4 12" />
                    </svg>
                  ) : (
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-destructive mx-auto">
                      <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
                    </svg>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {/* "View more" button */}
      {hidden > 0 && (
        <div className="p-4 text-center border-t border-border">
          <button
            className="text-primary text-xs font-semibold hover:underline cursor-pointer"
            onClick={() => setShowAll(!showAll)}
          >
            {showAll ? 'Collapse entries' : `View ${hidden} more entries...`}
          </button>
        </div>
      )}
    </div>
  );
}
