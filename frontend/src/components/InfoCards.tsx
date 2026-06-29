import { useState, useEffect, useRef } from 'react';
import { fetchTmdbSeasonMap, fetchBangumiEpisodes } from '@/api/torrentApi';
import { searchBangumi } from '@/api/rssApi';

interface Props {
  searchResult: any;
  onEpisodeDataChange: (augmented: any) => void;
}

interface Candidate {
  bangumi_id: number;
  name: string;
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
  // Also from episode_data (sequels / specials)
  for (const [idStr, data] of Object.entries(episodeData.tmdb || {})) {
    const id = Number(idStr);
    if (!tmdbEntries.has(id)) tmdbEntries.set(id, (data as any)?.name || `ID ${id}`);
  }
  for (const [idStr, data] of Object.entries(episodeData.bangumi || {})) {
    const id = Number(idStr);
    if (!bangumiEntries.has(id)) bangumiEntries.set(id, (data as any)?.name || `ID ${id}`);
  }

  // ── TMDB state ──
  const [tmdbInput, setTmdbInput] = useState('');
  const [tmdbLoading, setTmdbLoading] = useState(false);
  const [tmdbError, setTmdbError] = useState('');

  // ── Bangumi autocomplete state ──
  const [bgmInput, setBgmInput] = useState('');
  const [bgmLoading, setBgmLoading] = useState(false);
  const [bgmError, setBgmError] = useState('');
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [showDropdown, setShowDropdown] = useState(false);
  const [highlightIdx, setHighlightIdx] = useState(-1);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();
  const bgmContainerRef = useRef<HTMLDivElement>(null);

  // Click outside closes dropdown
  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (bgmContainerRef.current && !bgmContainerRef.current.contains(e.target as Node)) {
        setShowDropdown(false);
      }
    };
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  // ── Handlers ──

  const handleAddTmdb = async () => {
    const id = Number(tmdbInput.trim());
    if (!id || isNaN(id)) { setTmdbError('Invalid ID'); return; }
    setTmdbLoading(true);
    setTmdbError('');
    try {
      const seasons = await fetchTmdbSeasonMap(id);
      const newTmdb = { ...episodeData.tmdb };
      newTmdb[String(id)] = seasons;
      onEpisodeDataChange({ ...episodeData, tmdb: newTmdb });
      setTmdbInput('');
    } catch (e: any) {
      setTmdbError(e.message || 'Failed');
    } finally {
      setTmdbLoading(false);
    }
  };

  const handleAddBangumi = async (id: number) => {
    setBgmLoading(true);
    setBgmError('');
    try {
      const data = await fetchBangumiEpisodes(id);
      const newBgm = { ...episodeData.bangumi };
      newBgm[String(id)] = { name: data.name, episodes: data.episodes };
      onEpisodeDataChange({ ...episodeData, bangumi: newBgm });
      setBgmInput('');
      setCandidates([]);
    } catch (e: any) {
      setBgmError(e.message || 'Failed');
    } finally {
      setBgmLoading(false);
    }
  };

  // ── Bangumi autocomplete input ──

  const handleBgmInputChange = (value: string) => {
    setBgmInput(value);
    setHighlightIdx(-1);
    const trimmed = value.trim();
    if (trimmed && !/^\d+$/.test(trimmed) && trimmed.length >= 1) {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(async () => {
        try {
          const results = await searchBangumi(trimmed);
          setCandidates(results);
          setShowDropdown(results.length > 0);
        } catch {
          setCandidates([]);
          setShowDropdown(false);
        }
      }, 300);
    } else {
      setCandidates([]);
      setShowDropdown(false);
    }
  };

  const handleSelectCandidate = (c: Candidate) => {
    setBgmInput(String(c.bangumi_id));
    setShowDropdown(false);
    setCandidates([]);
    handleAddBangumi(c.bangumi_id);
  };

  const handleBgmKeyDown = (e: React.KeyboardEvent) => {
    if (showDropdown && candidates.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setHighlightIdx((prev) => Math.min(prev + 1, candidates.length - 1));
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        setHighlightIdx((prev) => Math.max(prev - 1, -1));
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (highlightIdx >= 0 && highlightIdx < candidates.length) {
          handleSelectCandidate(candidates[highlightIdx]);
        } else {
          const id = parseInt(bgmInput.trim(), 10);
          if (id && id > 0) handleAddBangumi(id);
        }
      } else if (e.key === 'Escape') {
        setShowDropdown(false);
        setHighlightIdx(-1);
      }
      return;
    }
    if (e.key === 'Enter') {
      const id = parseInt(bgmInput.trim(), 10);
      if (id && id > 0) handleAddBangumi(id);
    }
  };

  return (
    <div className="mb-8 bg-surface-light dark:bg-surface-dark border border-border-light dark:border-border-dark rounded-xl shadow-sm overflow-hidden">
      <div className="bg-slate-50 dark:bg-white/5 px-6 py-3 border-b border-border-light dark:border-border-dark flex justify-between items-center">
        <h3 className="font-bold text-sm">Metadata Source Overrides</h3>
        <span className="text-[10px] bg-primary/10 text-primary px-2 py-0.5 rounded-full font-bold uppercase">Manual Mapping</span>
      </div>

      <div className="p-6 grid grid-cols-1 md:grid-cols-2 gap-8 relative z-20">
        {/* ── TMDB Match ── */}
        <div className="space-y-3">
          <div className="flex items-center gap-2 mb-2">
            <span className="bg-[#01b4e4] w-2 h-6 rounded-full"></span>
            <h4 className="font-bold text-sm tracking-tight text-[#01b4e4]">TMDB Match</h4>
          </div>
          <div className="text-xs space-y-1 text-slate-500 dark:text-slate-400 italic">
            {tmdbEntries.size === 0 && <p>No match</p>}
            {[...tmdbEntries].map(([id, name]) => (
              <p key={id}>{name} ({id})</p>
            ))}
          </div>
          <div className="flex gap-2">
            <input
              className="flex-1 text-sm bg-slate-50 dark:bg-white/5 border-border-light dark:border-border-dark rounded-lg py-2"
              placeholder="Search TMDB ID..."
              value={tmdbInput}
              onChange={(e) => { setTmdbInput(e.target.value); setTmdbError(''); }}
              onKeyDown={(e) => e.key === 'Enter' && handleAddTmdb()}
            />
            <button
              className="px-4 bg-primary text-white text-sm font-bold rounded-lg hover:brightness-105 transition-all cursor-pointer disabled:opacity-50"
              onClick={handleAddTmdb}
              disabled={tmdbLoading}
            >
              {tmdbLoading ? '...' : 'Add'}
            </button>
          </div>
          {tmdbError && <div className="text-xs text-destructive mt-1">{tmdbError}</div>}
        </div>

        {/* ── Bangumi Match ── */}
        <div className="space-y-3">
          <div className="flex items-center gap-2 mb-2">
            <span className="bg-primary w-2 h-6 rounded-full"></span>
            <h4 className="font-bold text-sm tracking-tight text-primary">Bangumi Match</h4>
          </div>
          <div className="text-xs space-y-1 text-slate-500 dark:text-slate-400 italic">
            {bangumiEntries.size === 0 && <p>No match</p>}
            {[...bangumiEntries].map(([id, name]) => (
              <p key={id}>{name} ({id})</p>
            ))}
          </div>
          <div className="relative flex gap-2" ref={bgmContainerRef}>
            <input
              type="text"
              className="flex-1 text-sm bg-slate-50 dark:bg-white/5 border-border-light dark:border-border-dark rounded-lg py-2 outline-none"
              placeholder="Search Name or Bangumi ID..."
              value={bgmInput}
              onChange={(e) => handleBgmInputChange(e.target.value)}
              onFocus={() => { if (candidates.length > 0) setShowDropdown(true); }}
              onKeyDown={handleBgmKeyDown}
            />
            <button
              className="px-4 bg-primary text-white text-sm font-bold rounded-lg hover:brightness-105 transition-all cursor-pointer disabled:opacity-50"
              onClick={() => {
                const id = parseInt(bgmInput.trim(), 10);
                if (id && id > 0) handleAddBangumi(id);
              }}
              disabled={bgmLoading}
            >
              {bgmLoading ? '...' : 'Add'}
            </button>

            {/* Autocomplete dropdown */}
            {showDropdown && candidates.length > 0 && (
              <div className="absolute top-full left-0 right-16 mt-1 bg-card border border-border rounded-lg shadow-lg z-50 max-h-60 overflow-y-auto">
                {candidates.map((c, idx) => (
                  <div
                    key={c.bangumi_id}
                    className={`px-3 py-2 cursor-pointer text-xs flex justify-between items-center ${
                      idx === highlightIdx ? 'bg-primary/10 text-primary' : 'hover:bg-muted'
                    }`}
                    onMouseEnter={() => setHighlightIdx(idx)}
                    onClick={() => handleSelectCandidate(c)}
                  >
                    <span className="truncate">{c.name}</span>
                    <span className="text-[10px] text-muted-foreground ml-2 shrink-0">ID: {c.bangumi_id}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
          {bgmError && <div className="text-xs text-destructive mt-1">{bgmError}</div>}
        </div>
      </div>
    </div>
  );
}