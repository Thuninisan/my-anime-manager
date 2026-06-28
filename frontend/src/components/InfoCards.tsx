import { useState } from 'react';
import { fetchTmdbSeasonMap, fetchBangumiEpisodes } from '@/api/torrentApi';

interface Props {
  searchResult: any;
  onEpisodeDataChange: (augmented: any) => void;
}

export default function InfoCards({ searchResult, onEpisodeDataChange }: Props) {
  const searchResults = searchResult?.search_results || {};
  const episodeData = searchResult?.episode_data || { tmdb: {}, bangumi: {} };

  // Collect unique TMDB / Bangumi entries from search_results
  const tmdbEntries = new Map<number, string>();
  const bangumiEntries = new Map<number, string>();
  for (const entry of Object.values(searchResults) as any[]) {
    if (entry?.tmdb?.id && !tmdbEntries.has(entry.tmdb.id)) {
      tmdbEntries.set(entry.tmdb.id, entry.tmdb.name || `ID ${entry.tmdb.id}`);
    }
    if (entry?.bangumi?.id && !bangumiEntries.has(entry.bangumi.id)) {
      bangumiEntries.set(
        entry.bangumi.id,
        entry.bangumi.name_cn || entry.bangumi.name || `ID ${entry.bangumi.id}`,
      );
    }
  }

  // Also collect from episode_data (may include sequels / specials not in search_results)
  for (const [idStr, data] of Object.entries(episodeData.tmdb || {})) {
    const id = Number(idStr);
    if (!tmdbEntries.has(id)) {
      tmdbEntries.set(id, (data as any)?.name || `ID ${id}`);
    }
  }
  for (const [idStr, data] of Object.entries(episodeData.bangumi || {})) {
    const id = Number(idStr);
    if (!bangumiEntries.has(id)) {
      bangumiEntries.set(id, (data as any)?.name || `ID ${id}`);
    }
  }

  // Input states
  const [tmdbInput, setTmdbInput] = useState('');
  const [bgmInput, setBgmInput] = useState('');
  const [tmdbLoading, setTmdbLoading] = useState(false);
  const [bgmLoading, setBgmLoading] = useState(false);
  const [tmdbError, setTmdbError] = useState('');
  const [bgmError, setBgmError] = useState('');

  const handleAddTmdb = async () => {
    const id = Number(tmdbInput.trim());
    if (!id || isNaN(id)) { setTmdbError('Invalid ID'); return; }
    setTmdbLoading(true);
    setTmdbError('');
    try {
      const seasons = await fetchTmdbSeasonMap(id);
      const newTmdb = { ...episodeData.tmdb };
      newTmdb[String(id)] = seasons;
      const newEpisodeData = { ...episodeData, tmdb: newTmdb };
      onEpisodeDataChange(newEpisodeData);
      setTmdbInput('');
    } catch (e: any) {
      setTmdbError(e.message || 'Failed');
    } finally {
      setTmdbLoading(false);
    }
  };

  const handleAddBangumi = async () => {
    const id = Number(bgmInput.trim());
    if (!id || isNaN(id)) { setBgmError('Invalid ID'); return; }
    setBgmLoading(true);
    setBgmError('');
    try {
      const data = await fetchBangumiEpisodes(id);
      const newBgm = { ...episodeData.bangumi };
      newBgm[String(id)] = { name: data.name, episodes: data.episodes };
      const newEpisodeData = { ...episodeData, bangumi: newBgm };
      onEpisodeDataChange(newEpisodeData);
      setBgmInput('');
    } catch (e: any) {
      setBgmError(e.message || 'Failed');
    } finally {
      setBgmLoading(false);
    }
  };

  return (
    <div className="max-w-full mx-auto mt-4 flex gap-4">
      {/* ── TMDB Info Card ── */}
      <div className="flex-1 glass-card rounded-xl p-4">
        <div className="text-xs font-semibold text-primary mb-2">TMDB</div>
        <ul className="text-xs text-muted-foreground mb-3 space-y-0.5">
          {tmdbEntries.size === 0 && <li className="italic">No match</li>}
          {[...tmdbEntries].map(([id, name]) => (
            <li key={id}>
              <span className="text-foreground">{name}</span>
              <span className="ml-1 opacity-60">({id})</span>
            </li>
          ))}
        </ul>
        <div className="flex gap-1.5">
          <input
            className="flex-1 h-7 rounded border border-border bg-background px-2 text-xs outline-none focus:ring-1 focus:ring-ring"
            placeholder="TMDB ID"
            value={tmdbInput}
            onChange={(e) => { setTmdbInput(e.target.value); setTmdbError(''); }}
            onKeyDown={(e) => e.key === 'Enter' && handleAddTmdb()}
          />
          <button
            className="inline-flex items-center h-7 rounded bg-primary text-primary-foreground px-3 text-xs font-semibold hover:bg-primary/85 transition cursor-pointer disabled:opacity-50"
            onClick={handleAddTmdb}
            disabled={tmdbLoading}
          >
            {tmdbLoading ? '...' : 'Add'}
          </button>
        </div>
        {tmdbError && <div className="text-xs text-destructive mt-1">{tmdbError}</div>}
      </div>

      {/* ── Bangumi Info Card ── */}
      <div className="flex-1 glass-card rounded-xl p-4">
        <div className="text-xs font-semibold text-primary mb-2">Bangumi</div>
        <ul className="text-xs text-muted-foreground mb-3 space-y-0.5">
          {bangumiEntries.size === 0 && <li className="italic">No match</li>}
          {[...bangumiEntries].map(([id, name]) => (
            <li key={id}>
              <span className="text-foreground">{name}</span>
              <span className="ml-1 opacity-60">({id})</span>
            </li>
          ))}
        </ul>
        <div className="flex gap-1.5">
          <input
            className="flex-1 h-7 rounded border border-border bg-background px-2 text-xs outline-none focus:ring-1 focus:ring-ring"
            placeholder="Bangumi ID"
            value={bgmInput}
            onChange={(e) => { setBgmInput(e.target.value); setBgmError(''); }}
            onKeyDown={(e) => e.key === 'Enter' && handleAddBangumi()}
          />
          <button
            className="inline-flex items-center h-7 rounded bg-primary text-primary-foreground px-3 text-xs font-semibold hover:bg-primary/85 transition cursor-pointer disabled:opacity-50"
            onClick={handleAddBangumi}
            disabled={bgmLoading}
          >
            {bgmLoading ? '...' : 'Add'}
          </button>
        </div>
        {bgmError && <div className="text-xs text-destructive mt-1">{bgmError}</div>}
      </div>
    </div>
  );
}