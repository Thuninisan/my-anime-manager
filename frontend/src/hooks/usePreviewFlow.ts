import { useState, useCallback } from 'react';
import type {
  AppState,
  TorrentPreviewResponse,
  ConfirmResponse,
} from '../types/preview';
import * as api from '../api/torrentApi';
import { showError } from '../lib/toast';

export function usePreviewFlow() {
  const [state, setState] = useState<AppState>('idle');
  const [previewData, setPreviewData] = useState<TorrentPreviewResponse | null>(null);
  const [confirmResult, setConfirmResult] = useState<ConfirmResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const uploadTorrent = useCallback(async (file: File) => {
    setState('uploading');
    setError(null);
    setPreviewData(null);
    setConfirmResult(null);

    try {
      const data = await api.uploadPreview(file);
      setPreviewData(data);
      setState('preview');
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Upload failed';
      setError(message);
      setState('error');
      showError(err);
    }
  }, []);

  const confirmTorrent = useCallback(async () => {
    if (!previewData) return;

    setState('confirming');
    setError(null);

    try {
      const result = await api.confirmTorrent(previewData);
      setConfirmResult(result);
      setState('done');
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Confirm failed';
      setError(message);
      setState('error');
      showError(err);
    }
  }, [previewData]);

  const reset = useCallback(() => {
    setState('idle');
    setPreviewData(null);
    setConfirmResult(null);
    setError(null);
  }, []);

  return {
    state,
    previewData,
    confirmResult,
    error,
    uploadTorrent,
    confirmTorrent,
    reset,
  };
}
