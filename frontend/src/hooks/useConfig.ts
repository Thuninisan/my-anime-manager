import { useState, useEffect, useCallback } from 'react';
import type { AppConfig } from '@/types/preview';
import * as api from '@/api/torrentApi';

interface UseConfigReturn {
  config: AppConfig | null;
  loading: boolean;
  error: string | null;
  dirty: Partial<AppConfig>;
  saved: boolean;
  handleChange: (key: keyof AppConfig, value: string) => void;
  handleSave: () => Promise<void>;
}

export function useConfig(): UseConfigReturn {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dirty, setDirty] = useState<Partial<AppConfig>>({});
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.getConfig()
      .then(cfg => { setConfig(cfg); setLoading(false); })
      .catch(err => {
        setError(err instanceof Error ? err.message : 'Failed');
        setLoading(false);
      });
  }, []);

  const handleChange = useCallback((key: keyof AppConfig, value: string) => {
    if (!config) return;
    const current = config[key];
    let newVal: string | number = value;
    if (typeof current === 'number') {
      newVal = value === '' ? 0 : parseInt(value, 10);
      if (isNaN(newVal as number)) return;
    }
    setDirty(prev => {
      const next = { ...prev };
      if (newVal === current) delete next[key];
      else (next as Record<string, unknown>)[key] = newVal;
      return next;
    });
    setConfig(prev => prev ? { ...prev, [key]: newVal } : prev);
  }, [config]);

  const handleSave = useCallback(async () => {
    if (Object.keys(dirty).length === 0) return;
    setError(null); setSaved(false);
    try {
      const updated = await api.updateConfig(dirty);
      setConfig(updated); setDirty({}); setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed');
    }
  }, [dirty]);

  return { config, loading, error, dirty, saved, handleChange, handleSave };
}
