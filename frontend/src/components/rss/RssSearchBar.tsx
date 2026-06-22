import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card, CardContent } from '@/components/ui/card';
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
    <Card>
      <CardContent className="pt-4">
        <div className="flex gap-2">
          <div className="relative flex-1">
            <span className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none">
              <IconSearch />
            </span>
            <Input
              type="number"
              className="flex-1 pl-8"
              placeholder="Bangumi ID, e.g. 467461"
              value={bangumiId}
              onChange={e => onBangumiIdChange(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') onSearch(); }}
            />
          </div>
          <Button onClick={onSearch} disabled={searching}>
            {searching ? '查询中...' : '查询'}
          </Button>
        </div>
        {searchError && <p className="mt-2 text-sm text-destructive">{searchError}</p>}
      </CardContent>
    </Card>
  );
}
