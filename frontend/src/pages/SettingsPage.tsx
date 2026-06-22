import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import GeneralConfigForm from '@/components/settings/GeneralConfigForm';
import QbitConfigForm from '@/components/settings/QbitConfigForm';
import RssToolsPanel from '@/components/settings/RssToolsPanel';
import { useConfig } from '@/hooks/useConfig';

function TabButton({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "w-full px-3 py-2 rounded-md text-sm font-medium text-left transition-colors cursor-pointer",
        active
          ? "bg-sidebar-accent text-sidebar-accent-foreground"
          : "text-muted-foreground hover:text-foreground hover:bg-muted/50",
      )}
    >
      {label}
    </button>
  );
}

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
    <div className="max-w-2xl mx-auto">
      <div className="glass-card rounded-xl sakura-shadow overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-border">
          <h2 className="text-lg font-semibold">Settings</h2>
          <button
            className="text-muted-foreground hover:text-foreground text-xl w-8 h-8 flex items-center justify-center rounded cursor-pointer"
            onClick={() => navigate('/torrent')}
          >
            ✕
          </button>
        </div>

        {/* Body */}
        <div className="flex min-h-[500px]">
          {loading ? (
            <div className="flex-1 flex flex-col items-center justify-center gap-3 py-8">
              <div className="w-8 h-8 border-4 border-primary/30 border-t-primary rounded-full animate-spin" />
              <p className="text-sm text-muted-foreground">Loading...</p>
            </div>
          ) : (
            <>
              <div className="shrink-0 w-36 border-r border-border flex flex-col gap-0.5 px-3 py-4 bg-muted/20">
                <TabButton label="配置" active={tab === 'config'} onClick={() => setTab('config')} />
                <TabButton label="qBittorrent" active={tab === 'qbit'} onClick={() => setTab('qbit')} />
                <TabButton label="RSS" active={tab === 'tools'} onClick={() => setTab('tools')} />
              </div>

              <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
                {error && <div className="text-destructive text-sm p-3 bg-destructive/10 rounded-lg">{error}</div>}

                {tab === 'config' && config && (
                  <GeneralConfigForm config={config} dirty={dirty} onChange={handleChange} />
                )}
                {tab === 'qbit' && config && (
                  <QbitConfigForm config={config} dirty={dirty} onChange={handleChange} />
                )}
                {tab === 'tools' && config && (
                  <RssToolsPanel config={config} onChange={handleChange} />
                )}
              </div>
            </>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-6 py-4 border-t border-border">
          {saved ? <span className="text-sm text-success">Settings saved</span> : <span />}
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => navigate('/torrent')}>Close</Button>
            {(tab === 'config' || tab === 'qbit') && (
              <Button onClick={onSave} disabled={!hasChanges || saving}>
                {saving ? 'Saving...' : 'Save'}
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
