import type { BangumiRssResponse, RssFeedResponse, SubscriptionOut } from '@/types/preview';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { DropdownMenuRoot, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem } from '@/components/ui/dropdown-menu';
import TagFilterPanel from './TagFilterPanel';
import FeedPreview from './FeedPreview';

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
}

export default function SubtitleGroupTable({
  result, subscriptions, expanded, loadingFeed, filterTags, tagBoxOpen,
  onToggleFeed, onToggleTag, onToggleTagBox, onSubscribe, getSubMode,
}: Props) {
  if (result.groups.length === 0) {
    return <p className="text-center py-6 text-muted-foreground text-sm">未找到字幕组</p>;
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-8" />
          <TableHead>字幕组</TableHead>
          <TableHead className="hidden md:table-cell">RSS</TableHead>
          <TableHead>筛选标签</TableHead>
          <TableHead className="w-24" />
        </TableRow>
      </TableHeader>

      {result.groups.map(g => {
        const feed = expanded[g.rss_url];
        const loading = loadingFeed[g.rss_url] || false;
        const subMode = getSubMode(g.subgroup_id);
        const selectedTags = filterTags[g.subgroup_id] || [];
        const boxOpen = tagBoxOpen[g.subgroup_id] || false;
        const anyRowExpanded = feed !== undefined || boxOpen;

        return (
          <TableBody key={g.subgroup_id}>
            <TableRow className={anyRowExpanded ? 'border-b-0' : ''}>
              <TableCell>
                <button
                  className="w-7 h-7 flex items-center justify-center rounded border border-border text-muted-foreground hover:text-foreground hover:border-foreground/30 cursor-pointer text-xs bg-transparent"
                  onClick={() => onToggleFeed(g.rss_url)}
                  title={feed !== undefined ? '收起' : '展开'}
                >
                  {loading ? '⏳' : feed !== undefined ? '▲' : '▼'}
                </button>
              </TableCell>
              <TableCell className="font-medium">{g.name}</TableCell>
              <TableCell className="hidden md:table-cell">
                <a href={g.rss_url} target="_blank" rel="noreferrer" className="text-primary text-xs break-all">
                  {g.rss_url}
                </a>
              </TableCell>
              <TableCell>
                {subMode ? (
                  <span className="text-xs text-muted-foreground">—</span>
                ) : (
                  <button
                    className={`text-xs px-2 py-1 rounded border cursor-pointer transition ${
                      boxOpen
                        ? 'bg-primary/15 border-primary/40 text-primary'
                        : 'border-border text-muted-foreground hover:text-foreground hover:border-foreground/30'
                    }`}
                    onClick={() => onToggleTagBox(g.subgroup_id)}
                  >
                    🏷 可选标签
                    {selectedTags.length > 0 && (
                      <span className="ml-1 bg-primary/20 text-primary px-1 rounded text-[10px]">{selectedTags.length}</span>
                    )}
                  </button>
                )}
              </TableCell>
              <TableCell>
                {subMode ? (
                  <span className={`inline-block px-3 py-1 rounded-full text-xs font-medium ${
                    subMode === 'primary'
                      ? 'bg-primary/15 text-primary'
                      : 'bg-amber-500/15 text-amber-600 dark:text-amber-400'
                  }`}>
                    {subMode === 'primary' ? '主订阅' : '备用订阅'}
                  </span>
                ) : (
                  <DropdownMenuRoot>
                    <DropdownMenuTrigger className="h-7 text-[0.8rem]">订阅 ▾</DropdownMenuTrigger>
                    <DropdownMenuContent>
                      <DropdownMenuItem onClick={() => onSubscribe(g, 'primary')}>作为主 RSS 订阅</DropdownMenuItem>
                      <DropdownMenuItem onClick={() => onSubscribe(g, 'backup')}>作为备用 RSS 订阅</DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenuRoot>
                )}
              </TableCell>
            </TableRow>

            {/* Tag filter row */}
            {boxOpen && !subMode && (
              <TableRow>
                <TableCell colSpan={5} className="pt-0">
                  <TagFilterPanel
                    selectedTags={selectedTags}
                    onToggleTag={(tag) => onToggleTag(g.subgroup_id, tag)}
                  />
                </TableCell>
              </TableRow>
            )}

            {/* Feed preview row */}
            {feed !== undefined && (
              <TableRow>
                <TableCell colSpan={5} className="pt-0">
                  {feed === null ? (
                    <p className="py-4 text-center text-muted-foreground text-sm">获取失败</p>
                  ) : (
                    <FeedPreview items={feed.items} selectedTags={filterTags[g.subgroup_id] || []} />
                  )}
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        );
      })}
    </Table>
  );
}
