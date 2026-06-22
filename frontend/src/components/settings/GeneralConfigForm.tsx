import type { AppConfig } from '@/types/preview';
import FieldGroup from '@/components/shared/FieldGroup';

interface FieldDef { key: keyof AppConfig; label: string; placeholder: string; type: 'text' | 'number' | 'password'; hint: string; }

const FIELDS: FieldDef[] = [
  { key: 'TMDB_API_KEY', label: 'TMDB API Key', placeholder: 'your TMDB API key', type: 'password', hint: 'From https://www.themoviedb.org/settings/api' },
  { key: 'BANGUMI_UA', label: 'Bangumi User-Agent', placeholder: 'JellyfinTmdbHelper/1.0', type: 'text', hint: 'Custom User-Agent for Bangumi API requests' },
  { key: 'API_DELAY_MS', label: 'API Delay (ms)', placeholder: '600', type: 'number', hint: 'Delay between API calls to avoid rate limiting' },
  { key: 'PROXY_HOST', label: 'Proxy Host', placeholder: '127.0.0.1', type: 'text', hint: 'HTTP proxy host (leave empty to disable)' },
  { key: 'PROXY_PORT', label: 'Proxy Port', placeholder: '7890', type: 'number', hint: 'HTTP proxy port' },
  { key: 'TORRENT_WATCH_DIR', label: 'Torrent Watch Directory', placeholder: '/data/torrent', type: 'text', hint: 'Directory to watch for .torrent files (scan mode)' },
  { key: 'MIKAN_BASE_URL', label: 'Mikan Base URL', placeholder: 'https://mikanani.me', type: 'text', hint: 'Mikanani.me base URL' },
];

interface Props {
  config: AppConfig;
  dirty: Partial<AppConfig>;
  onChange: (key: keyof AppConfig, value: string) => void;
}

export default function GeneralConfigForm({ config, dirty, onChange }: Props) {
  return (
    <div className="space-y-4">
      {FIELDS.map(f => {
        const val = config[f.key];
        const display = typeof val === 'number' ? String(val) : (val as string);
        const masked = display === '***';
        return (
          <FieldGroup
            key={f.key}
            id={`cfg-${f.key}`}
            label={f.label}
            hint={f.hint}
            type={f.type}
            value={masked ? '' : display}
            placeholder={masked ? '••• (unchanged)' : f.placeholder}
            dirty={f.key in dirty}
            onChange={v => onChange(f.key, v)}
          />
        );
      })}
    </div>
  );
}
