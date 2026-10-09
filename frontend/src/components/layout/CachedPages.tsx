import { Activity, useEffect, useState } from 'react';
import { Navigate, Route, Routes, useLocation, type Location } from 'react-router-dom';
import TorrentPage from '@/pages/TorrentPage';
import RssPage from '@/pages/RssPage';
import SettingsPage from '@/pages/SettingsPage';
import ResourcesPage from '@/pages/ResourcesPage';
import ExplorePage from '@/pages/ExplorePage';

const CACHE_TTL_MS = 5 * 60 * 1000;
const PAGE_PATHS = new Set(['/torrent', '/rss', '/settings', '/resources', '/explore']);

interface CachedPage {
  location: Location;
  expiresAt: number | null;
}

export default function CachedPages() {
  const location = useLocation();
  const [cache, setCache] = useState<{ location: Location; pages: CachedPage[] }>(
    () => ({ location, pages: PAGE_PATHS.has(location.pathname) ? [{ location, expiresAt: null }] : [] }),
  );

  // Update before rendering children so inactive pages retain their own route state.
  if (cache.location !== location) {
    // This timestamp is sampled only when the router supplies a new location.
    // eslint-disable-next-line react-hooks/purity
    const now = Date.now();
    const pages = cache.pages
      .filter((page) => page.expiresAt === null || page.expiresAt > now)
      .map((page) => page.location.pathname === location.pathname
        ? { location, expiresAt: null }
        : { ...page, expiresAt: page.expiresAt ?? now + CACHE_TTL_MS });
    if (PAGE_PATHS.has(location.pathname) && !pages.some((page) => page.location.pathname === location.pathname)) {
      pages.push({ location, expiresAt: null });
    }
    setCache({ location, pages });
  }

  useEffect(() => {
    const deadlines = cache.pages.flatMap((page) => page.expiresAt === null ? [] : [page.expiresAt]);
    if (!deadlines.length) return;
    const timer = window.setTimeout(() => {
      setCache((current) => ({
        ...current,
        pages: current.pages.filter((page) => page.expiresAt === null || page.expiresAt > Date.now()),
      }));
    }, Math.max(0, Math.min(...deadlines) - Date.now()));
    return () => window.clearTimeout(timer);
  }, [cache]);

  return (
    <>
      {location.pathname === '/' && <Navigate to="/torrent" replace />}
      {location.pathname === '/resource' && <Navigate to="/resources" replace />}
      {cache.pages.map((page) => (
        <Activity key={page.location.pathname} mode={page.location.pathname === location.pathname ? 'visible' : 'hidden'}>
          <Routes location={page.location}>
            <Route path="torrent" element={<TorrentPage />} />
            <Route path="rss" element={<RssPage />} />
            <Route path="settings" element={<SettingsPage />} />
            <Route path="resources" element={<ResourcesPage />} />
            <Route path="explore" element={<ExplorePage />} />
          </Routes>
        </Activity>
      ))}
    </>
  );
}
