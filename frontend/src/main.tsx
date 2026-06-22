import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { createBrowserRouter, RouterProvider, Navigate } from 'react-router-dom';
import AppLayout from '@/components/layout/AppLayout';
import TorrentPage from '@/pages/TorrentPage';
import RssPage from '@/pages/RssPage';
import SettingsPage from '@/pages/SettingsPage';
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
    ],
  },
]);

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);
