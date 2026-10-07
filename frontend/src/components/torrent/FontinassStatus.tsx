import { useState } from 'react';
import { retryFontinass, type TorrentCollection } from '@/api/torrentApi';

const labels: Record<string, string> = {
  waiting: '等待复制字幕', processing: '字体处理中', disabled: '字体处理未启用',
  not_needed: '无 ASS 字幕', success: '字体处理完成', partial: '部分字幕处理失败', failed: '字体处理失败',
};

export default function FontinassStatus({ item, onRefresh }: { item: TorrentCollection; onRefresh: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const state = item.fontinass;
  if (!state || state.status === 'not_needed' || (state.status === 'disabled' && !state.files.length)) return null;
  const failed = state.files.filter(file => file.status === 'failed');
  const success = state.files.filter(file => file.status === 'success').length;
  const retry = async () => {
    setBusy(true);
    setError('');
    try {
      await retryFontinass(item.info_hash);
      await onRefresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '重试失败');
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="mt-2 rounded-lg border border-border p-2 text-xs text-muted-foreground">
      <p role="status">{labels[state.status] || state.status}{state.files.length > 0 && ` · ${success}/${state.files.length} 成功`}</p>
      {failed.length > 0 && <details className="mt-1">
        <summary className="cursor-pointer">{failed.length} 个字幕保留原文件</summary>
        <ul className="mt-2 space-y-2">
          {failed.map(file => <li key={file.path} className="break-words">
            <p>{file.path.split('/').pop()}</p><p>{file.error || '处理失败'}</p>
          </li>)}
        </ul>
      </details>}
      {failed.length > 0 && item.status !== 'downloading' && state.status !== 'processing' &&
        <button type="button" disabled={busy} onClick={() => void retry()} className="mt-2 text-primary hover:underline disabled:opacity-50 cursor-pointer">
          {busy ? '正在提交…' : '重试失败字幕'}
        </button>}
      {error && <p role="alert" className="mt-1 text-destructive">{error}</p>}
    </div>
  );
}
