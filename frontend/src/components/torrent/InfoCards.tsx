import { catalogSeriesTitle } from '@/lib/episodeAdapters';
import type { TorrentPreviewResponse } from '@/types/preview';
import type { EpisodeCatalog } from '@/types/episode';
import { useState, useEffect, useRef } from 'react';
import { augmentPreview } from '@/api/torrentApi';
import { searchBangumi } from '@/api/rssApi';

interface Props {
  searchResult: TorrentPreviewResponse;
  /** The current (merged) episode_catalog from the parent, including the user's
   *  own additions.  Falls back to searchResult.episode_catalog when omitted. */
  episodeDataOverride?: EpisodeCatalog;
  onEpisodeDataChange: (augmented: EpisodeCatalog) => void;
  onPreviewChange: (view: TorrentPreviewResponse) => void;
}

interface Candidate {
  bangumi_id: number;
  name: string;
}

export default function InfoCards({ searchResult, episodeDataOverride, onPreviewChange }: Props) {
  const searchResults = searchResult?.search_results || {};
  const showKeys = Object.keys(searchResults);
  const [selectedShowKey, setSelectedShowKey] = useState('');
  const targetShowKey = showKeys.includes(selectedShowKey) ? selectedShowKey : (showKeys.length === 1 ? showKeys[0] : '');
  // Use the parent-merged data when available so the TMDB/Bangumi Match
  // display reflects the user's own additions immediately.
  const episodeData = episodeDataOverride ?? (searchResult?.episode_catalog || { tmdb: {}, bangumi: {} });

  // Collect unique TMDB / Bangumi entries from search_results
  const tmdbEntries = new Map<number, string>();
  const bangumiEntries = new Map<number, string>();
  for (const entry of Object.values(searchResults)) {
    if (entry?.tmdb_series_id && !tmdbEntries.has(entry.tmdb_series_id)) {
      tmdbEntries.set(entry.tmdb_series_id, entry.display_name || `ID ${entry.tmdb_series_id}`);
    }
    if (entry?.bangumi_subject_id && !bangumiEntries.has(entry.bangumi_subject_id)) {
      bangumiEntries.set(
        entry.bangumi_subject_id,
        entry.bangumi_display_name || entry.bangumi_display_name || `ID ${entry.bangumi_subject_id}`,
      );
    }
  }
  // Also from episode_catalog (sequels / specials / manually added).
  // Provider show-name sentinels are normalized into tmdb_series_titles.
  for (const idStr of Object.keys(episodeData.tmdb || {})) {
    const id = Number(idStr);
    if (!tmdbEntries.has(id)) {
      const label = catalogSeriesTitle(episodeData, idStr) || `ID ${id}`;
      tmdbEntries.set(id, label);
    }
  }
  for (const [idStr, data] of Object.entries(episodeData.bangumi || {})) {
    const id = Number(idStr);
    if (!bangumiEntries.has(id)) bangumiEntries.set(id, data.name || `ID ${id}`);
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
  const debounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
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

  const handleAddTmdb = async (candidateId?: number) => {
    const id = candidateId ?? Number(tmdbInput.trim());
    if (!id || isNaN(id)) { setTmdbError('Invalid ID'); return; }
    setTmdbLoading(true);
    setTmdbError('');
    try {
      onPreviewChange(await augmentPreview(searchResult, 'tmdb', id, targetShowKey));
      setTmdbInput('');
    } catch (e) {
      setTmdbError(e instanceof Error ? e.message : 'Failed');
    } finally {
      setTmdbLoading(false);
    }
  };

  const handleAddBangumi = async (id: number) => {
    setBgmLoading(true);
    setBgmError('');
    try {
      onPreviewChange(await augmentPreview(searchResult, 'bangumi', id, targetShowKey));
      setBgmInput('');
      setCandidates([]);
    } catch (e) {
      setBgmError(e instanceof Error ? e.message : 'Failed');
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
    <div className="mb-8 bg-surface-light dark:bg-surface-dark border border-border-light dark:border-border-dark rounded-xl shadow-sm">
      <div className="bg-slate-50 dark:bg-white/5 px-6 py-3 border-b border-border-light dark:border-border-dark flex justify-between items-center">
        <h3 className="font-bold text-sm">Metadata Source Overrides</h3>
        <span className="text-[10px] bg-primary/10 text-primary px-2 py-0.5 rounded-full font-bold uppercase">Manual Mapping</span>
      </div>

      {showKeys.length > 1 && (
        <label className="flex items-center gap-3 px-6 pt-4 text-sm">
          Series to update
          <select className="rounded border border-border bg-background px-3 py-2" value={targetShowKey}
            onChange={(event) => setSelectedShowKey(event.target.value)}>
            <option value="">Select series</option>
            {showKeys.map((key) => <option key={key} value={key}>{key}</option>)}
          </select>
        </label>
      )}

      <div className="p-6 grid grid-cols-1 md:grid-cols-2 gap-8 relative z-20">
        {/* ── TMDB Match ── */}
        <div className="space-y-3">
          <div className="flex items-center gap-2 mb-2">
            <span className="bg-[#01b4e4] w-2 h-6 rounded-full"></span>
            <h4 className="font-bold text-sm tracking-tight text-[#01b4e4]">TMDB Match</h4>
          </div>
          <div className="text-xs space-y-1 text-slate-500 dark:text-slate-400 italic">
            {tmdbEntries.size === 0 && <p>No confirmed animation match. Add a TMDB ID manually below.</p>}
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
              onClick={() => handleAddTmdb()}
              disabled={tmdbLoading}
            >
              {tmdbLoading ? '...' : 'Add'}
            </button>
          </div>
          {targetShowKey && searchResults[targetShowKey]?.provider_resolutions?.tmdb?.status === 'ambiguous' && (
            <div className="space-y-2 text-xs">
              <p>{targetShowKey}: 需要确认 TMDB 资源</p>
              {searchResults[targetShowKey].provider_resolutions?.tmdb.candidates.map((candidate) => (
                <button key={candidate.provider_id} type="button"
                  className="block w-full text-left rounded-lg border border-border p-2 hover:bg-muted disabled:opacity-50"
                  disabled={tmdbLoading} onClick={() => handleAddTmdb(candidate.provider_id)}>
                  确认 {candidate.title || candidate.original_title || 'TMDB'} ({candidate.provider_id})
                </button>
              ))}
            </div>
          )}
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
          {targetShowKey && searchResults[targetShowKey]?.provider_resolutions?.bangumi?.status === 'ambiguous' && (
            <div className="space-y-2 text-xs">
              <p>{targetShowKey}: 需要确认 Bangumi 资源</p>
              {searchResults[targetShowKey].provider_resolutions?.bangumi.candidates.map((candidate) => (
                <button key={candidate.provider_id} type="button"
                  className="block w-full text-left rounded-lg border border-border p-2 hover:bg-muted disabled:opacity-50"
                  disabled={bgmLoading} onClick={() => handleAddBangumi(candidate.provider_id)}>
                  确认 {candidate.title || candidate.original_title || 'Bangumi'} ({candidate.provider_id})
                </button>
              ))}
            </div>
          )}
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
