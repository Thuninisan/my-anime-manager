import { useState } from 'react';
import type { SubtitleFile, SubtitleState } from '@/lib/subtitleMatching';

export default function SubtitlePanel({ state, files, onAssociate, onDelete }: {
  state: SubtitleState; files: SubtitleFile[];
  onAssociate: (id: string, linked: boolean) => void;
  onDelete: (file: SubtitleFile) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState('');
  const pending = state.candidates.length > 0;
  const count = state.linked.length;
  return <div className="mt-2 text-xs">
    <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
      className={`inline-flex items-center gap-2 cursor-pointer ${pending ? 'text-amber-500' : count ? 'text-emerald-600 dark:text-emerald-400' : 'text-muted-foreground'}`}>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><rect x="2" y="5" width="20" height="14" rx="2"/><path d="M6 10h4m4 0h4M6 14h7m2 0h3"/></svg>
      <span>{pending ? `字幕待确认 · ${state.candidates.length} 个候选` : count ? `字幕 ${count} · 种子 ${state.linked.filter(s => s.source === 'torrent').length} / 上传 ${state.linked.filter(s => s.source === 'upload').length}` : '未关联字幕'}</span>
      <span className="underline">{open ? '收起' : count ? '查看' : '选择'}</span>
    </button>
    {open && <div className="mt-2 space-y-2 rounded-lg border border-border p-3">
      <p className="text-muted-foreground">选择外置字幕；内封字幕不在此统计。</p>
      {files.length === 0 && <p className="text-muted-foreground">暂无外置字幕，可上传字幕文件。</p>}
      {files.map(file => <div key={file.id} className="flex items-start gap-2">
        <label className="flex flex-1 min-w-0 items-start gap-2 cursor-pointer">
          <input type="checkbox" checked={state.linked.some(s => s.id === file.id)} onChange={e => onAssociate(file.id, e.target.checked)} />
          <span className="break-all" title={file.path}>{file.name}<span className="text-muted-foreground"> · {file.source === 'torrent' ? '种子' : '上传'} · {file.path.split('.').pop()?.toUpperCase()}</span>{file.source === 'torrent' && <span className="block text-muted-foreground">{file.path}</span>}</span>
        </label>
        {file.source === 'upload' && <button type="button" className="text-destructive cursor-pointer shrink-0" onClick={async () => { try { await onDelete(file); setError(''); } catch (e) { setError(e instanceof Error ? e.message : '删除失败'); } }}>删除</button>}
      </div>)}
      {error && <p role="alert" className="text-destructive">{error}</p>}
    </div>}
  </div>;
}
