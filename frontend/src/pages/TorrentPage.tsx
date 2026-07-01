import { useState } from 'react';
import TorrentUpload from '@/components/TorrentUpload';
import TorrentPreview from '@/components/TorrentPreview';
import { parseAndSearchTorrent } from '@/api/torrentApi';

export default function TorrentPage() {
  const [searchResult, setSearchResult] = useState<any>(null);
  const [augmentedEpData, setAugmentedEpData] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  // Parse-and-search handler for the upload dropzone
  const handleParseTorrent = async (file: File) => {
    setError(null);
    try {
      const result = await parseAndSearchTorrent(file);
      setSearchResult(result);
      setAugmentedEpData(null);
    } catch (err: any) {
      setError(err.message || 'Unknown error');
      setSearchResult(null);
    }
  };

  return (
    <>
      {/* Upload dropzone */}
      {!searchResult && !error && (
        <TorrentUpload onParse={handleParseTorrent} />
      )}

      {/* Error */}
      {error && (
        <div className="flex items-center justify-center py-20">
          <div className="glass-card rounded-xl p-8 text-center max-w-[500px] w-full sakura-shadow border-l-4 border-l-destructive">
            <div className="w-14 h-14 bg-destructive/10 rounded-full flex items-center justify-center mx-auto mb-4">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-destructive">
                <circle cx="12" cy="12" r="10" />
                <line x1="12" y1="8" x2="12" y2="12" />
                <line x1="12" y1="16" x2="12.01" y2="16" />
              </svg>
            </div>
            <h2 className="text-lg font-semibold text-destructive mb-2">Processing Failed</h2>
            <p className="text-sm text-muted-foreground mb-6 whitespace-pre-wrap">{error}</p>
            <button
              className="inline-flex items-center gap-2 rounded-lg bg-primary text-primary-foreground px-5 py-2.5 text-sm font-semibold hover:bg-primary/85 shadow-md shadow-primary/15 transition cursor-pointer"
              onClick={() => setError(null)}
            >
              Try Again
            </button>
          </div>
        </div>
      )}

      {/* Match table / preview (parse-and-search result) */}
      {searchResult && !searchResult.error && searchResult.parsed_files && (
        <TorrentPreview
          searchResult={searchResult}
          augmentedEpData={augmentedEpData}
          onEpisodeDataChange={setAugmentedEpData}
          onClose={() => { setSearchResult(null); setAugmentedEpData(null); }}
        />
      )}

      {/* Parse error from server */}
      {searchResult?.error && (
        <div className="max-w-4xl mx-auto mt-4 glass-card rounded-xl p-4">
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm font-semibold text-destructive">Error</span>
            <button
              className="text-muted-foreground hover:text-foreground text-lg leading-none cursor-pointer"
              onClick={() => setSearchResult(null)}
            >
              &times;
            </button>
          </div>
          <pre className="text-xs text-muted-foreground">{searchResult.error}</pre>
        </div>
      )}
    </>
  );
}
