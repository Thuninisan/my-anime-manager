import { useState, useEffect, useRef } from 'react';
import { IconSearch } from '@/components/icons';
import { searchBangumi } from '@/api/rssApi';

interface Props {
  bangumiId: string;
  searching: boolean;
  searchError: string;
  onBangumiIdChange: (v: string) => void;
  onSearch: (id: number) => void;
}

interface Candidate {
  bangumi_id: number;
  name: string;
}

export default function RssSearchBar({ bangumiId, searching, searchError, onBangumiIdChange, onSearch }: Props) {
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [showDropdown, setShowDropdown] = useState(false);
  const [highlightIdx, setHighlightIdx] = useState(-1);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setShowDropdown(false);
      }
    };
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  const handleInputChange = (value: string) => {
    onBangumiIdChange(value);
    setHighlightIdx(-1);

    const trimmed = value.trim();
    // Only suggest for text (non-numeric) queries
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

  const handleSelectCandidate = (candidate: Candidate) => {
    onBangumiIdChange(String(candidate.bangumi_id));
    setShowDropdown(false);
    setCandidates([]);
    onSearch(candidate.bangumi_id);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (showDropdown && candidates.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setHighlightIdx(prev => Math.min(prev + 1, candidates.length - 1));
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        setHighlightIdx(prev => Math.max(prev - 1, -1));
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (highlightIdx >= 0 && highlightIdx < candidates.length) {
          handleSelectCandidate(candidates[highlightIdx]);
        } else {
          onSearch(parseInt(bangumiId.trim(), 10) || 0);
        }
      } else if (e.key === 'Escape') {
        setShowDropdown(false);
        setHighlightIdx(-1);
      }
      return;
    }
    if (e.key === 'Enter') {
      const id = parseInt(bangumiId.trim(), 10);
      if (id && id > 0) onSearch(id);
    }
  };

  return (
    <div className="space-y-2" ref={containerRef}>
      <div className="relative flex items-center gap-3 bg-card p-1 rounded-xl shadow-sm border border-border w-full max-w-md">
        <span className="pl-3 text-muted-foreground">
          <IconSearch />
        </span>
        <input
          type="text"
          className="border-0 focus:ring-0 bg-transparent flex-grow text-sm py-2 outline-none"
          placeholder="名称或 Bangumi ID (e.g. 地狱乐)"
          value={bangumiId}
          onChange={e => handleInputChange(e.target.value)}
          onFocus={() => { if (candidates.length > 0) setShowDropdown(true); }}
          onKeyDown={handleKeyDown}
        />
        <button
          className="bg-primary text-primary-foreground px-4 py-2 rounded-lg text-xs font-semibold hover:brightness-110 active:scale-95 transition-all cursor-pointer disabled:opacity-50"
          onClick={() => {
            const id = parseInt(bangumiId.trim(), 10);
            if (id && id > 0) onSearch(id);
          }}
          disabled={searching}
        >
          {searching ? 'Searching...' : 'Subscribe'}
        </button>

        {/* Dropdown candidates */}
        {showDropdown && candidates.length > 0 && (
          <div className="absolute top-full left-0 right-0 mt-1 bg-card border border-border rounded-lg shadow-lg z-10 max-h-60 overflow-y-auto">
            {candidates.map((c, idx) => (
              <div
                key={c.bangumi_id}
                className={`px-3 py-2 cursor-pointer text-sm flex justify-between items-center ${
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
      {searchError && <p className="text-sm text-destructive">{searchError}</p>}
    </div>
  );
}
