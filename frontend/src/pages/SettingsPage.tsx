import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import GeneralConfigForm from '@/components/settings/GeneralConfigForm';
import QbitConfigForm from '@/components/settings/QbitConfigForm';
import RssToolsPanel from '@/components/settings/RssToolsPanel';
import { useConfig } from '@/hooks/useConfig';

/* Tab definitions */
const TABS = [
  { key: 'config', label: 'General', icon: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="3"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg> },
  { key: 'qbit', label: 'qBittorrent', icon: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg> },
  { key: 'tools', label: 'RSS Tools', icon: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M4 11a9 9 0 0 1 9 9"/><path d="M4 4a16 16 0 0 1 16 16"/><circle cx="5" cy="19" r="1"/></svg> },
] as const;

export default function SettingsPage() {
  const navigate = useNavigate();
  const [tab, setTab] = useState('config');
  const { config, loading, error, dirty, saved, handleChange, handleSave } = useConfig();
  const [saving, setSaving] = useState(false);

  const onSave = async () => {
    setSaving(true);
    await handleSave();
    setSaving(false);
  };

  const hasChanges = Object.keys(dirty).length > 0;

  return (
    <>
      {/* ── Top Bar ── */}
      <div className="flex items-end justify-between mb-6">
        <div>
          <h2 className="text-2xl font-bold text-primary mb-1">Configuration</h2>
          <p className="text-sm text-muted-foreground">
            Manage your API keys, system paths, and network connections.
          </p>
        </div>
        <div className="flex gap-3">
          <Button variant="outline" onClick={() => navigate('/torrent')}>
            Discard
          </Button>
          <Button
            onClick={onSave}
            disabled={!hasChanges || saving}
            className="shadow-md shadow-primary/15 flex items-center gap-2"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z" />
              <polyline points="17 21 17 13 7 13 7 21" />
              <polyline points="7 3 7 8 15 8" />
            </svg>
            {saving ? 'Saving...' : 'Save Changes'}
          </Button>
        </div>
      </div>

      {error && (
        <div className="mb-6 text-destructive text-sm p-3 bg-destructive/10 rounded-lg">{error}</div>
      )}
      {saved && (
        <div className="mb-6 text-success text-sm p-3 bg-success/10 rounded-lg">Settings saved successfully.</div>
      )}

      {/* ── Content: 3/9 grid ── */}
      {loading ? (
        <div className="flex items-center justify-center py-20">
          <div className="w-8 h-8 border-4 border-primary/30 border-t-primary rounded-full animate-spin" />
        </div>
      ) : config ? (
        <div className="grid grid-cols-12 gap-6 items-start">
          {/* Left: Vertical Tab Navigation */}
          <div className="col-span-3 flex flex-col gap-2 sticky top-20">
            {TABS.map(t => (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                className={cn(
                  "flex items-center gap-3 p-4 rounded-xl text-left transition-all cursor-pointer",
                  tab === t.key
                    ? "bg-accent text-accent-foreground font-bold shadow-sm"
                    : "text-muted-foreground hover:bg-muted/50",
                )}
              >
                <span className="text-lg">{t.icon}</span>
                <span className="text-sm font-semibold">{t.label}</span>
              </button>
            ))}
          </div>

          {/* Right: Form Sections */}
          <div className="col-span-9">
            {tab === 'config' && (
              <GeneralConfigForm config={config} dirty={dirty} onChange={handleChange} />
            )}
            {tab === 'qbit' && (
              <QbitConfigForm config={config} dirty={dirty} onChange={handleChange} />
            )}
            {tab === 'tools' && (
              <RssToolsPanel config={config} onChange={handleChange} />
            )}
          </div>
        </div>
      ) : null}

      {/* Floating decoration */}
      <div className="fixed bottom-12 right-12 pointer-events-none opacity-10">
        <svg width="120" height="120" viewBox="0 0 24 24" fill="currentColor" className="text-primary rotate-12">
          <path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5c-1.38 0-2.5-1.12-2.5-2.5S10.62 6.5 12 6.5s2.5 1.12 2.5 2.5-1.12 2.5-2.5 2.5z" />
        </svg>
      </div>
    </>
  );
}
