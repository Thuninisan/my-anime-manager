import type { NfoRefreshTask } from '@/types/nfoRefresh';

export default function NfoRefreshStatus({ task, error }: { task: NfoRefreshTask | null; error: string }) {
  if (!task && !error) return null;
  const active = task?.status === 'queued' || task?.status === 'running';
  const labels = { show: '剧集 NFO', season: '季 NFO', episode: '分集 NFO' };
  return (
    <div className="px-5 py-3 border-b border-border text-sm shrink-0" aria-live="polite">
      {error && <p className="text-destructive">{error}，正在重试获取进度。</p>}
      {task && <>
        <div className="flex flex-wrap justify-between gap-2">
          <span>{task.status === 'queued' ? '等待刷新' : active ? '正在刷新全部 NFO' : task.message}
            {active && task.current_sort !== null ? ` · EP${String(task.current_sort).padStart(2, '0')}` : ''}
          </span>
          <span className="text-muted-foreground">已处理 {task.processed}/{task.total} 集</span>
        </div>
        {active && <progress className="w-full h-2 mt-2 accent-primary" value={task.processed} max={task.total} aria-label="NFO 刷新进度" />}
        <p className="mt-1 text-xs text-muted-foreground">
          已刷新：剧集 {task.counts.show} 个 · 季 {task.counts.season} 个 · 分集 {task.counts.episode} 个
          {' '}｜分集成功 {task.succeeded} · 跳过 {task.skipped} · 失败 {task.failed}
        </p>
        {task.results.length > 0 && <details className="mt-2">
          <summary className="cursor-pointer text-muted-foreground">查看刷新明细（{task.results.length} 项）</summary>
          <ul className="mt-2 max-h-32 overflow-y-auto space-y-2 text-xs">
            {task.results.map((result, index) => <li key={index} className={result.status === 'failed' ? 'text-destructive' : 'text-muted-foreground'}>
              <span>EP{String(result.sort).padStart(2, '0')} · {labels[result.kind]} · {result.message}</span>
              {result.warning && <span> · {result.warning}</span>}
              {result.path && <p className="break-all mt-0.5">{result.path}</p>}
            </li>)}
          </ul>
        </details>}
      </>}
    </div>
  );
}
