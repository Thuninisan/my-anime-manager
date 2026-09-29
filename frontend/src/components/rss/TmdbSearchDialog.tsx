import { useEffect, useState } from 'react';
import type { SeasonInfo } from '@/types/preview';
import { searchTmdbShows, setSubscriptionTmdb, getTmdbSeasonMap } from '@/api/rssApi';
import {
  DialogRoot, DialogContent, DialogHeader, DialogTitle,
  DialogBody, DialogFooter, DialogClose,
} from '@/components/ui/dialog';

const TMDB_IMAGE_BASE = 'https://image.tmdb.org/t/p/w92';

interface TmdbSearchResult {
  id: number;
  name: string;
  original_name: string;
  first_air_date: string;
  poster_path: string;
}

interface Props {
  open: boolean;
  bangumiId: number;
  bangumiName: string;
  onClose: () => void;
  onAssigned: (tmdbId: number, tmdbSeason: number | null) => void;
}

export default function TmdbSearchDialog({
  open, bangumiId, bangumiName, onClose, onAssigned,
}: Props) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<TmdbSearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState('');

  // Selected TMDB show
  const [selectedShow, setSelectedShow] = useState<TmdbSearchResult | null>(null);
  const [seasons, setSeasons] = useState<Record<string, SeasonInfo>>({});
  const [selectedSeason, setSelectedSeason] = useState<number | null>(null);
  const [loadingSeasons, setLoadingSeasons] = useState(false);
  const [saving, setSaving] = useState(false);

  // Reset state when dialog opens
  useEffect(() => {
    if (!open) return;
    setQuery(bangumiName);
    setResults([]);
    setSearching(false);
    setError('');
    setSelectedShow(null);
    setSeasons({});
    setSelectedSeason(null);
    setLoadingSeasons(false);
    setSaving(false);
    // Auto-search on open
    handleSearch(bangumiName);
  }, [open, bangumiName]);

  async function handleSearch(q: string) {
    setQuery(q);
    if (!q.trim()) return;
    setSearching(true);
    setError('');
    setResults([]);
    setSelectedShow(null);
    try {
      const res = await searchTmdbShows(q.trim());
      setResults(res);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'TMDB 搜索失败');
    } finally {
      setSearching(false);
    }
  }

  async function handleSelectShow(show: TmdbSearchResult) {
    setSelectedShow(show);
    setSelectedSeason(null);
    setSeasons({});
    setLoadingSeasons(true);
    try {
      const map = await getTmdbSeasonMap(show.id);
      setSeasons(map);
      // Default to season 1 if available
      if (map['1']) setSelectedSeason(1);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '获取季数据失败');
    } finally {
      setLoadingSeasons(false);
    }
  }

  async function handleConfirm() {
    if (!selectedShow) return;
    setSaving(true);
    try {
      await setSubscriptionTmdb(bangumiId, selectedShow.id, selectedSeason);
      onAssigned(selectedShow.id, selectedSeason);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '保存失败');
    } finally {
      setSaving(false);
    }
  }

  const seasonKeys = Object.keys(seasons).map(Number).sort((a, b) => a - b);

  return (
    <DialogRoot open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>搜索 TMDB 条目</DialogTitle>
          <DialogClose onClick={onClose} />
        </DialogHeader>

        <DialogBody>
          <div className="space-y-4">
            {/* Search input */}
            <div className="flex gap-2">
              <input
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') handleSearch(query); }}
                placeholder="输入 TMDB 节目名称..."
                className="flex-1 h-9 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
              />
              <button
                className="inline-flex items-center justify-center h-9 rounded-md bg-primary text-primary-foreground px-4 text-sm font-medium hover:bg-primary/90 disabled:opacity-50"
                onClick={() => handleSearch(query)}
                disabled={searching || !query.trim()}
              >
                {searching ? '...' : 'Search'}
              </button>
            </div>

            {error && (
              <p className="text-sm text-destructive">{error}</p>
            )}

            {/* Results list */}
            {results.length > 0 && !selectedShow && (
              <div className="max-h-64 overflow-y-auto space-y-1 border rounded-md">
                {results.map((r) => (
                  <button
                    key={r.id}
                    className="w-full flex items-center gap-3 px-3 py-2 text-left hover:bg-accent transition-colors"
                    onClick={() => handleSelectShow(r)}
                  >
                    {r.poster_path ? (
                      <img
                        src={`${TMDB_IMAGE_BASE}${r.poster_path}`}
                        alt=""
                        className="w-8 h-12 rounded object-cover shrink-0 bg-muted"
                      />
                    ) : (
                      <div className="w-8 h-12 rounded bg-muted shrink-0 flex items-center justify-center text-[10px] text-muted-foreground">
                        N/A
                      </div>
                    )}
                    <div className="min-w-0">
                      <p className="text-sm font-medium truncate">{r.name}</p>
                      <p className="text-xs text-muted-foreground">
                        {r.original_name}{r.first_air_date ? ` (${r.first_air_date.slice(0, 4)})` : ''}
                      </p>
                    </div>
                  </button>
                ))}
              </div>
            )}

            {searching && (
              <p className="text-sm text-muted-foreground">Searching...</p>
            )}
            {!searching && !error && results.length === 0 && query && (
              <p className="text-sm text-muted-foreground">No results</p>
            )}

            {/* Selected show + season picker */}
            {selectedShow && (
              <div className="space-y-3 border rounded-md p-3">
                <div className="flex items-center gap-3">
                  {selectedShow.poster_path ? (
                    <img
                      src={`${TMDB_IMAGE_BASE}${selectedShow.poster_path}`}
                      alt=""
                      className="w-10 h-[60px] rounded object-cover shrink-0 bg-muted"
                    />
                  ) : (
                    <div className="w-10 h-[60px] rounded bg-muted shrink-0" />
                  )}
                  <div className="min-w-0">
                    <p className="text-sm font-semibold">{selectedShow.name}</p>
                    <p className="text-xs text-muted-foreground">
                      {selectedShow.original_name}
                      {selectedShow.first_air_date ? ` (${selectedShow.first_air_date.slice(0, 4)})` : ''}
                    </p>
                    <button
                      className="text-xs text-primary hover:underline mt-0.5"
                      onClick={() => { setSelectedShow(null); setSeasons({}); setSelectedSeason(null); }}
                    >
                      Change
                    </button>
                  </div>
                </div>

                {/* Season picker */}
                {loadingSeasons ? (
                  <p className="text-xs text-muted-foreground">Loading seasons...</p>
                ) : seasonKeys.length > 0 ? (
                  <div>
                    <p className="text-xs font-medium mb-1.5">Season</p>
                    <div className="flex flex-wrap gap-1.5">
                      {seasonKeys.map((sn) => {
                        const s = seasons[String(sn)];
                        const isSelected = selectedSeason === sn;
                        return (
                          <button
                            key={sn}
                            className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
                              isSelected
                                ? 'bg-primary text-primary-foreground border-primary'
                                : 'border-input hover:bg-accent'
                            }`}
                            onClick={() => setSelectedSeason(sn)}
                          >
                            {sn === 0 ? 'Specials' : `S${String(sn).padStart(2, '0')}`}
                            <span className="ml-1 text-[10px] opacity-70">
                              ({s.episodes.length} ep)
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  </div>
                ) : null}
              </div>
            )}
          </div>
        </DialogBody>

        <DialogFooter>
          <DialogClose onClick={onClose}>Cancel</DialogClose>
          <button
            className="inline-flex items-center justify-center h-9 rounded-md bg-primary text-primary-foreground px-4 text-sm font-medium hover:bg-primary/90 disabled:opacity-50"
            onClick={handleConfirm}
            disabled={!selectedShow || saving}
          >
            {saving ? 'Saving...' : 'Confirm'}
          </button>
        </DialogFooter>
      </DialogContent>
    </DialogRoot>
  );
}
