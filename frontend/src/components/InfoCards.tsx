import { useState, useEffect, useRef } from 'react';
import { IconSearch } from '@/components/icons';
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
    <div className="max-w-full mx-auto mt-4 flex gap-4 relative z-20">
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

      {/* ── Bangumi Info Card (with autocomplete) ── */}
      <div className="flex-1 glass-card rounded-xl p-4 overflow-visible">
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
        <div className="relative flex items-center gap-1.5 bg-background rounded border border-border" ref={bgmContainerRef}>
          <span className="pl-2 text-muted-foreground shrink-0">
            <IconSearch />
          </span>
          <input
            type="text"
            className="flex-1 h-7 bg-transparent text-xs outline-none border-0"
            placeholder="名称或 Bangumi ID"
            value={bgmInput}
            onChange={(e) => handleBgmInputChange(e.target.value)}
            onFocus={() => { if (candidates.length > 0) setShowDropdown(true); }}
            onKeyDown={handleBgmKeyDown}
          />
          <button
            className="h-7 rounded-r bg-primary text-primary-foreground px-3 text-xs font-semibold hover:bg-primary/85 transition cursor-pointer disabled:opacity-50 shrink-0"
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
            <div className="absolute top-full left-0 right-0 mt-1 bg-card border border-border rounded-lg shadow-lg z-50 max-h-60 overflow-y-auto">
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
  );
}