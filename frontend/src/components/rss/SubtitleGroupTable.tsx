import { useState } from 'react';
import type { BangumiRssResponse, RssFeedResponse, SubscriptionOut } from '@/types/preview';
import TagFilterPanel from './TagFilterPanel';
import FeedPreview from './FeedPreview';
import {
  DropdownMenuRoot, DropdownMenuTrigger,
  DropdownMenuContent, DropdownMenuItem,
} from '@/components/ui/dropdown-menu';

interface Props {
  result: BangumiRssResponse;
  subscriptions: SubscriptionOut[];
  expanded: Record<string, RssFeedResponse | null>;
  loadingFeed: Record<string, boolean>;
  filterTags: Record<number, string[]>;
  tagBoxOpen: Record<number, boolean>;
  onToggleFeed: (url: string) => void;
  onToggleTag: (subgroupId: number, tag: string) => void;
  onToggleTagBox: (subgroupId: number) => void;
  onSubscribe: (group: { name: string; subgroup_id: number; rss_url: string }, role: 'primary' | 'backup') => void;
  getSubMode: (subgroupId: number) => 'primary' | 'backup' | null;
  subscribingId: number | null;
}

function CopyButton({ url }: { url: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard.writeText(url).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <span
      className="p-0.5 hover:bg-muted rounded transition-colors cursor-pointer shrink-0 inline-flex items-center"
      onClick={handleCopy}
      title="Copy RSS URL"
    >
      {copied ? (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-emerald-500">
          <polyline points="20 6 9 17 4 12" />
        </svg>
      ) : (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-muted-foreground">
          <rect x="9" y="9" width="13" height="13" rx="2" ry="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
        </svg>
      )}
    </span>
  );
}

export default function SubtitleGroupTable({
  result, subscriptions, expanded, loadingFeed, filterTags, tagBoxOpen,
  onToggleFeed, onToggleTag, onToggleTagBox, onSubscribe, getSubMode,
  subscribingId,
}: Props) {
  if (result.groups.length === 0) {
    return <p className="text-center py-6 text-muted-foreground text-sm">No subtitle groups found</p>;
  }

  return (
    <div className="divide-y divide-border">
      {result.groups.map(g => {
        const feed = expanded[g.rss_url];
        const loading = loadingFeed[g.rss_url] || false;
        const subMode = getSubMode(g.subgroup_id);
        const selectedTags = filterTags[g.subgroup_id] || [];
        const boxOpen = tagBoxOpen[g.subgroup_id] || false;
        const isExpanded = feed !== undefined;

        return (
          <div key={g.subgroup_id}>
            {/* ── Group header row ── */}
            <div
              className={`flex items-center justify-between px-5 py-3 cursor-pointer transition-colors ${
                isExpanded
                  ? 'bg-muted/30 border-b border-border'
                  : 'hover:bg-muted/20'
              }`}
              onClick={() => onToggleFeed(g.rss_url)}
            >
              {/* Left: chevron + name (RSS URL on hover) */}
              <div className="flex items-center gap-4 min-w-0 flex-1 group">
                {loading ? (
                  <div className="w-4 h-4 border-2 border-primary/30 border-t-primary rounded-full animate-spin shrink-0" />
                ) : (
                  <svg
                    width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                    strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
                    className={`shrink-0 transition-transform duration-300 ${isExpanded ? 'text-primary rotate-180' : 'text-muted-foreground'}`}
                  >
                    <polyline points="6 9 12 15 18 9" />
                  </svg>
                )}
                <div className="min-w-0 py-0.5">
                  <h4 className={`text-sm font-semibold ${isExpanded ? 'text-primary' : 'text-foreground'}`}>
                    {g.name}
                  </h4>
                  <div className="hidden group-hover:flex items-center gap-1 mt-0.5">
                    <span className="text-[11px] text-muted-foreground font-mono truncate max-w-[300px]">
                      {g.rss_url}
                    </span>
                    <CopyButton url={g.rss_url} />
                  </div>
                </div>
              </div>

              {/* Right: filter + subscribe buttons */}
              <div className="flex items-center gap-3 shrink-0 ml-4" onClick={e => e.stopPropagation()}>
                {/* Filter tags button */}
                  <button
                    className={`text-xs px-2.5 py-1.5 rounded-full border transition cursor-pointer font-semibold ${
                      boxOpen
                        ? 'bg-primary/10 border-primary/30 text-primary'
                        : 'border-border text-muted-foreground hover:text-foreground hover:border-foreground/30'
                    }`}
                    onClick={() => onToggleTagBox(g.subgroup_id)}
                  >
                    Tags{selectedTags.length > 0 ? ` (${selectedTags.length})` : ''}
                  </button>

                {/* Subscribe buttons — three states */}
                {subMode ? (
                  // Subscribed: show single role label with color distinction
                  subMode === 'primary' ? (
                    <span className="text-xs px-2.5 py-1.5 rounded-full bg-primary/15 text-primary font-semibold">
                      已订阅: 主
                    </span>
                  ) : (
                    <span className="text-xs px-2.5 py-1.5 rounded-full bg-amber-500/15 text-amber-600 dark:text-amber-400 font-semibold">
                      已订阅: 副
                    </span>
                  )
                ) : subscribingId === g.subgroup_id ? (
                  // Subscribing: show spinner
                  <span className="text-xs px-2.5 py-1.5 rounded-full bg-primary/10 text-primary font-semibold inline-flex items-center gap-1 cursor-default">
                    订阅
                    <div className="w-3.5 h-3.5 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
                  </span>
                ) : (
                  // Not subscribed: dropdown
                  <DropdownMenuRoot>
                    <DropdownMenuTrigger className="text-xs px-2.5 py-1.5 rounded-full bg-primary/10 text-primary font-semibold hover:bg-primary hover:text-primary-foreground">
                      订阅
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="ml-1">
                        <polyline points="6 9 12 15 18 9" />
                      </svg>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="min-w-[140px]">
                      <DropdownMenuItem onClick={() => onSubscribe(g, 'primary')}>
                        作为主 RSS 订阅
                      </DropdownMenuItem>
                      <DropdownMenuItem onClick={() => onSubscribe(g, 'backup')}>
                        作为副 RSS 订阅
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenuRoot>
                )}
              </div>
            </div>

            {/* ── Tag filter panel ── */}
            {boxOpen && (
              <div className="px-5 py-2 bg-muted/20 border-b border-border">
                <TagFilterPanel
                  selectedTags={selectedTags}
                  onToggleTag={(tag) => onToggleTag(g.subgroup_id, tag)}
                />
              </div>
            )}

            {/* ── Expanded feed ── */}
            {isExpanded && (
              <div className="bg-muted/10">
                {feed === null ? (
                  <p className="py-6 text-center text-muted-foreground text-sm">Failed to load feed</p>
                ) : (
                  <FeedPreview items={feed.items} selectedTags={filterTags[g.subgroup_id] || []} />
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
