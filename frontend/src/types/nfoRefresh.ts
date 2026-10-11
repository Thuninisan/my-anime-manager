export interface NfoRefreshResult {
  kind: 'show' | 'season' | 'episode';
  sort: number;
  path: string;
  status: 'success' | 'skipped' | 'failed';
  message: string;
  warning?: string;
}

export interface NfoRefreshTask {
  task_id: string;
  bangumi_id: number;
  status: 'queued' | 'running' | 'completed' | 'partial_failed' | 'failed';
  total: number;
  processed: number;
  current_sort: number | null;
  succeeded: number;
  skipped: number;
  failed: number;
  counts: { show: number; season: number; episode: number };
  results: NfoRefreshResult[];
  message: string;
}
