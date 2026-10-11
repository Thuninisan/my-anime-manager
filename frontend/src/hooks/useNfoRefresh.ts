import { useCallback, useEffect, useRef, useState } from 'react';
import { getNfoRefreshTask, refreshAllNfo } from '@/api/rssApi';
import { showError, showSuccess } from '@/lib/toast';
import type { NfoRefreshTask } from '@/types/nfoRefresh';

const isActive = (task: NfoRefreshTask | null) => task?.status === 'queued' || task?.status === 'running';

export function useNfoRefresh(bangumiId: number | null, open: boolean, onRefresh: () => void) {
  const [task, setTask] = useState<NfoRefreshTask | null>(null);
  const [starting, setStarting] = useState(false);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState('');
  const taskRef = useRef<NfoRefreshTask | null>(null);
  const requestRevision = useRef(0);
  const selectedId = useRef(bangumiId);
  selectedId.current = bangumiId;
  const onRefreshRef = useRef(onRefresh);
  onRefreshRef.current = onRefresh;

  const accept = useCallback((next: NfoRefreshTask | null) => {
    const previous = taskRef.current;
    taskRef.current = next;
    setTask(next);
    if (next && !isActive(next) && previous?.task_id === next.task_id && isActive(previous)) {
      const summary = `NFO 刷新结束：剧集 ${next.counts.show} 个、季 ${next.counts.season} 个、分集 ${next.counts.episode} 个`;
      if (next.status === 'completed') showSuccess(summary);
      else showError(`${summary}；${next.message}`);
      onRefreshRef.current();
    }
  }, []);

  const running = task?.bangumi_id === bangumiId && isActive(task);
  useEffect(() => {
    if (bangumiId === null || (!open && !running)) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setChecking(true);
    const poll = async () => {
      const revision = requestRevision.current;
      let retry = false;
      try {
        const next = await getNfoRefreshTask(bangumiId);
        if (cancelled || revision !== requestRevision.current) return;
        setError('');
        accept(next);
      } catch (e) {
        if (!cancelled) {
          retry = true;
          setError(e instanceof Error ? e.message : String(e));
        }
      } finally {
        if (!cancelled) {
          setChecking(false);
          if (retry || (taskRef.current?.bangumi_id === bangumiId && isActive(taskRef.current))) timer = setTimeout(poll, retry ? 5000 : 2000);
        }
      }
    };
    void poll();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, [bangumiId, open, running, accept]);

  const start = async () => {
    if (bangumiId === null || starting || running) return;
    setStarting(true);
    try {
      const next = await refreshAllNfo(bangumiId);
      if (selectedId.current === bangumiId) {
        requestRevision.current += 1;
        setError('');
        accept(next);
      }
    } catch (e) {
      showError(e);
    } finally {
      setStarting(false);
    }
  };

  return { task: task?.bangumi_id === bangumiId ? task : null, busy: starting || running, checking, error, start };
}
