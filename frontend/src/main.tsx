import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { createBrowserRouter, RouterProvider, Navigate } from 'react-router-dom';
import AppLayout from '@/components/layout/AppLayout';
import TorrentPage from '@/pages/TorrentPage';
import RssPage from '@/pages/RssPage';
import SettingsPage from '@/pages/SettingsPage';
import ResourcesPage from '@/pages/ResourcesPage';
import './App.css';

const router = createBrowserRouter([
  {
    path: '/',
    element: <AppLayout />,
    children: [
      { index: true, element: <Navigate to="/torrent" replace /> },
      { path: 'torrent', element: <TorrentPage /> },
      { path: 'rss', element: <RssPage /> },
      { path: 'settings', element: <SettingsPage /> },
      { path: 'resources', element: <ResourcesPage /> },
      { path: 'resource', element: <Navigate to="/resources" replace /> },
    ],
  },
]);

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);
