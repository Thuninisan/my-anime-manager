import type { MappingChangePreview, MappingChangeTask } from '@/types/mapping';
import { Button } from '@/components/ui/button';

interface Props {
  preview: MappingChangePreview | null;
  task: MappingChangeTask | null;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  onRetry: () => void;
  onEdit: () => void;
}

export default function MappingChangeConfirmDialog({ preview, task, busy, onConfirm, onCancel, onRetry, onEdit }: Props) {
  if (task) {
    const active = task.status === 'queued' || task.status === 'running';
    return <div className="space-y-3 text-sm" aria-live="polite">
      <h3 className="font-semibold">{task.kind === 'cleanup' ? '清理旧订阅' : '重建 NFO 和单集缩略图'}</h3>
      <p>{task.message}</p>
      {task.kind === 'nfo' && <p>已处理 {task.processed ?? 0}/{task.total ?? 0} 集，成功 {task.succeeded ?? 0}，失败 {task.failed ?? 0}。</p>}
      {task.kind === 'nfo' && task.thumbnails && <p className="text-xs text-muted-foreground">单集缩略图：已更新 {task.thumbnails.updated} 张 · 新映射无图片 {task.thumbnails.missing} 集 · 重建失败 {task.thumbnails.failed} 集。</p>}
      {active && <progress className="w-full accent-primary" value={task.kind === 'nfo' ? task.processed ?? 0 : undefined} max={task.total || 1} aria-label="任务进度" />}
      {task.results.length > 0 && <details>
        <summary className="cursor-pointer">查看处理明细（{task.results.length} 项）</summary>
        <ul className="mt-2 max-h-40 overflow-y-auto space-y-1 text-xs">
          {task.results.map((item, index) => <li key={`${index}-${item.path}`} className={item.status === 'failed' ? 'text-destructive' : 'text-muted-foreground'}>
            {item.message} · <span className="break-all">{item.path}</span>
            {item.thumbnail_path && <p className="break-all">缩略图：{item.thumbnail_path}</p>}
          </li>)}
        </ul>
      </details>}
      {(task.status === 'failed' || task.status === 'partial_failed') && <Button disabled={busy} onClick={onRetry}>重试任务</Button>}
      {(task.status === 'failed' || task.status === 'partial_failed') && !task.deletion_started && <Button variant="outline" disabled={busy} onClick={onEdit}>返回编辑映射</Button>}
      {!active && <Button variant="outline" onClick={onCancel}>关闭</Button>}
    </div>;
  }
  if (!preview) return null;
  if (preview.kind === 'cleanup') return <div className="space-y-3 text-sm">
    <h3 className="font-semibold">即将更改 Mikan 映射</h3>
    <p>Mikan ID：{preview.old_mikan_id ?? '未关联'} → {preview.new_mikan_id ?? '清除关联'}</p>
    <p>确认后将删除旧订阅关联的下载文件、字幕和元数据，清理下载任务与下载记录，并解除旧主备 RSS 订阅。随后需要重新选择字幕组订阅。</p>
    <p className="text-muted-foreground">受影响：下载记录 {preview.episode_count} 条、下载任务 {preview.plan?.torrents.length ?? 0} 个、媒体文件 {preview.plan?.media_count ?? 0} 个、NFO {preview.plan?.nfo_count ?? 0} 个。</p>
    {preview.plan?.warnings.map(warning => <p key={warning} className="text-amber-600">{warning}</p>)}
    <div className="flex justify-end gap-2"><Button variant="outline" onClick={onCancel}>取消</Button><Button variant="destructive" disabled={busy} onClick={onConfirm}>{busy ? '处理中…' : '确认删除并更改映射'}</Button></div>
  </div>;
  return <div className="space-y-3 text-sm">
    <h3 className="font-semibold">已更改 NFO 平台映射，是否重新生成 NFO 和单集缩略图？</h3>
    <p>确认后将根据新的平台 ID、季数和集数偏移重新解析已有下载记录，更新 NFO 和每集缩略图。视频、字幕、剧集与季海报及下载任务保留。</p>
    <p className="text-muted-foreground">现有下载记录：{preview.episode_count} 条。新映射确认没有图片时会清理旧缩略图及引用；下载失败时保留本集旧文件，并显示失败原因。</p>
    <div className="flex flex-wrap justify-end gap-2"><Button variant="outline" disabled={busy} onClick={onCancel}>暂不生成</Button><Button disabled={busy || preview.episode_count === 0} onClick={onConfirm}>{busy ? '启动中…' : '重新生成 NFO 和缩略图'}</Button></div>
  </div>;
}
