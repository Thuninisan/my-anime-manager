import { useState, useCallback, useEffect, useMemo } from 'react';

type Theme = 'light' | 'dark';
const STORAGE_KEY = 'theme';

function getSystemTheme(): Theme {
  if (typeof window === 'undefined') return 'dark';
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function getStoredTheme(): Theme | null {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'light' || stored === 'dark') return stored as Theme;
  } catch {
    // localStorage unavailable (SSR / private browsing)
  }
  return null;
}

function resolveTheme(): Theme {
  return getStoredTheme() ?? getSystemTheme();
}

export function useTheme() {
  const [theme, setThemeState] = useState<Theme>(resolveTheme);

  // Sync class + localStorage whenever theme changes
  useEffect(() => {
    const root = document.documentElement;
    const isDark = theme === 'dark';
    root.classList.toggle('dark', isDark);
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      // Silently fail if localStorage is unavailable
    }
  }, [theme]);

  // Listen for system preference changes (only when no stored preference)
  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const handler = () => {
      if (getStoredTheme() === null) {
        // No stored preference: follow system
        setThemeState(mq.matches ? 'dark' : 'light');
      }
    };
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  }, []);

  const toggleTheme = useCallback(() => {
    setThemeState(prev => (prev === 'dark' ? 'light' : 'dark'));
  }, []);

  const setTheme = useCallback((t: Theme) => {
    setThemeState(t);
  }, []);

  return useMemo(() => ({ theme, toggleTheme, setTheme }), [theme, toggleTheme, setTheme]);
}
