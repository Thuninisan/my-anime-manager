import { IconSearch } from '@/components/icons';

interface Props {
  bangumiId: string;
  searching: boolean;
  searchError: string;
  onBangumiIdChange: (v: string) => void;
  onSearch: () => void;
}

export default function RssSearchBar({ bangumiId, searching, searchError, onBangumiIdChange, onSearch }: Props) {
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-3 bg-card p-1 rounded-xl shadow-sm border border-border w-full max-w-md">
        <span className="pl-3 text-muted-foreground">
          <IconSearch />
        </span>
        <input
          type="number"
          className="border-0 focus:ring-0 bg-transparent flex-grow text-sm py-2 outline-none"
          placeholder="Enter Bangumi ID (e.g. 402315)"
          value={bangumiId}
          onChange={e => onBangumiIdChange(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') onSearch(); }}
        />
        <button
          className="bg-primary text-primary-foreground px-4 py-2 rounded-lg text-xs font-semibold hover:brightness-110 active:scale-95 transition-all cursor-pointer disabled:opacity-50"
          onClick={onSearch}
          disabled={searching}
        >
          {searching ? 'Searching...' : 'Subscribe'}
        </button>
      </div>
      {searchError && <p className="text-sm text-destructive">{searchError}</p>}
    </div>
  );
}
