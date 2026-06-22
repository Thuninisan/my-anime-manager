import type { DownloadHistoryResponse } from '@/types/preview';
import { DialogRoot, DialogContent, DialogHeader, DialogTitle, DialogBody, DialogFooter, DialogClose } from '@/components/ui/dialog';

interface Props {
  open: boolean;
  data: DownloadHistoryResponse | null;
  loading: boolean;
  onClose: () => void;
}

export default function DownloadHistoryDialog({ open, data, loading, onClose }: Props) {
  return (
    <DialogRoot open={open} onOpenChange={(isOpen) => { if (!isOpen) onClose(); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>📋 {data?.name || '…'} · Season {data?.bgm_season || '?'}</DialogTitle>
          <DialogClose className="text-muted-foreground hover:text-foreground text-xl w-8 h-8 flex items-center justify-center rounded cursor-pointer">✕</DialogClose>
        </DialogHeader>
        <DialogBody>
          {loading ? (
            <div className="flex flex-col items-center gap-3 py-8">
              <div className="w-8 h-8 border-4 border-primary/30 border-t-primary rounded-full animate-spin" />
              <p className="text-sm text-muted-foreground">加载中...</p>
            </div>
          ) : !data ? (
            <p className="text-center py-8 text-muted-foreground text-sm">加载失败</p>
          ) : (
            <>
              {/* Stats */}
              <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm mb-4">
                <span className="text-muted-foreground">
                  已下载 <span className="font-medium text-foreground">{data.episodes.length}</span>
                  /{data.bgm_sortrange[1] - data.bgm_sortrange[0] + 1} 集
                </span>
                <span className="text-muted-foreground">
                  主源 <span className="font-medium text-foreground">{data.episodes.filter(e => e.source === 'primary').length}</span>
                </span>
                <span className="text-muted-foreground">
                  备源 <span className="font-medium text-foreground">{data.episodes.filter(e => e.source === 'backup').length}</span>
                </span>
                {data.missing_sorts.length > 0 && (
                  <span className="text-warning">缺少: EP{data.missing_sorts.join(', EP')}</span>
                )}
                {data.missing_sorts.length === 0 && data.episodes.length > 0 && (
                  <span className="text-success">✅ 已全部下载</span>
                )}
              </div>
              {/* Table */}
              <div className="max-h-80 overflow-y-auto rounded-lg border border-border">
                <table className="w-full text-sm">
                  <thead className="bg-muted/50 sticky top-0">
                    <tr>
                      <th className="px-3 py-2 text-left text-xs font-medium text-muted-foreground w-14">集号</th>
                      <th className="px-3 py-2 text-left text-xs font-medium text-muted-foreground w-12">来源</th>
                      <th className="px-3 py-2 text-left text-xs font-medium text-muted-foreground w-20">状态</th>
                      <th className="px-3 py-2 text-left text-xs font-medium text-muted-foreground">进度</th>
                      <th className="px-3 py-2 text-left text-xs font-medium text-muted-foreground">种子名称</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.episodes.sort((a, b) => a.sort - b.sort).map(e => {
                      const q = e.qbit;
                      const state = q ? q.state : '未下载';
                      const stateColor =
                        state === 'uploading' || state === 'stalledUP' ? 'text-success' :
                        state === 'downloading' ? 'text-info' :
                        state === 'pausedDL' || state === 'pausedUP' ? 'text-warning' :
                        state === 'queuedDL' || state === 'queuedUP' ? 'text-cyan-600 dark:text-cyan-400' :
                        state === 'missingFiles' ? 'text-destructive' : 'text-muted-foreground';
                      const progress = q ? (q.progress * 100).toFixed(0) + '%' : '—';
                      const stateLabel =
                        state === 'uploading' || state === 'stalledUP' ? '做种中' :
                        state === 'downloading' ? '下载中' :
                        state === 'pausedDL' || state === 'pausedUP' ? '已暂停' :
                        state === 'queuedDL' || state === 'queuedUP' ? '队列中' :
                        state === 'missingFiles' ? '缺文件' : state;
                      return (
                        <tr key={e.sort} className="border-t border-border hover:bg-muted/30">
                          <td className="px-3 py-2 font-medium tabular-nums">EP{e.sort.toString().padStart(2, '0')}</td>
                          <td className="px-3 py-2">
                            <span className={`text-xs px-1.5 py-0.5 rounded-full ${
                              e.source === 'primary'
                                ? 'bg-primary/15 text-primary'
                                : 'bg-amber-500/15 text-amber-600 dark:text-amber-400'
                            }`}>
                              {e.source === 'primary' ? '主' : '备'}
                            </span>
                          </td>
                          <td className={`px-3 py-2 text-xs ${stateColor}`}>{stateLabel}</td>
                          <td className="px-3 py-2">
                            {q ? (
                              <div className="flex items-center gap-2">
                                <div className="w-16 h-1.5 bg-muted rounded-full overflow-hidden">
                                  <div className={`h-full rounded-full transition ${q.progress >= 1 ? 'bg-success' : 'bg-primary'}`}
                                    style={{ width: `${Math.round(q.progress * 100)}%` }} />
                                </div>
                                <span className="text-xs text-muted-foreground tabular-nums">{progress}</span>
                              </div>
                            ) : <span className="text-xs text-muted-foreground">—</span>}
                          </td>
                          <td className="px-3 py-2 text-xs text-muted-foreground max-w-48 truncate" title={q?.name || e.guid}>
                            {q?.name || e.guid.slice(0, 60)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </DialogBody>
        <DialogFooter>
          <DialogClose className="inline-flex items-center justify-center rounded-lg border border-border bg-background px-2.5 py-1 text-[0.8rem] font-medium hover:bg-muted cursor-pointer">
            关闭
          </DialogClose>
        </DialogFooter>
      </DialogContent>
    </DialogRoot>
  );
}
