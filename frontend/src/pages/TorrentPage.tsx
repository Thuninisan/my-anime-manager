import { useCallback, useEffect, useState } from 'react';
import TorrentUpload from '@/components/torrent/TorrentUpload';
import TorrentPreview from '@/components/torrent/TorrentPreview';
import FontinassStatus from '@/components/torrent/FontinassStatus';
import PosterCard from '@/components/shared/PosterCard';
import { getTorrentCollections, parseAndSearchTorrent, type TorrentCollection } from '@/api/torrentApi';
import { useLocation, useNavigate } from 'react-router-dom';
import { previewResourceTorrent } from '@/api/resourcesApi';

export default function TorrentPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const replacement = location.state as { replaceBangumiId?: number; replaceName?: string; resourceId?: number } | null;
  const [searchResult, setSearchResult] = useState<any>(null);
  const [augmentedEpData, setAugmentedEpData] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [resourceLoading, setResourceLoading] = useState(false);
  const [collections, setCollections] = useState<TorrentCollection[]>([]);
  const [collectionsError, setCollectionsError] = useState<string | null>(null);
  const [collectionsLoading, setCollectionsLoading] = useState(true);

  const refreshCollections = useCallback(async () => {
    try {
      const items = await getTorrentCollections();
      setCollections(items);
      setCollectionsError(null);
    } catch (err) {
      setCollectionsError(err instanceof Error ? err.message : 'Failed to load torrents');
    } finally {
      setCollectionsLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshCollections();
    const timer = window.setInterval(() => { void refreshCollections(); }, 15000);
    return () => window.clearInterval(timer);
  }, [refreshCollections]);

  useEffect(() => {
    if (!replacement?.resourceId) return;
    let active = true;
    setResourceLoading(true);
    setError(null);
    setSearchResult(null);
    void previewResourceTorrent(replacement.resourceId).then((result) => {
      if (!active) return;
      setSearchResult(result);
      setAugmentedEpData(null);
    }).catch((cause) => {
      if (active) setError(cause instanceof Error ? cause.message : '资源种子预览失败');
    }).finally(() => {
      if (active) setResourceLoading(false);
    });
    return () => { active = false; };
  }, [replacement?.resourceId]);

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

  const handleClosePreview = () => {
    setSearchResult(null);
    setAugmentedEpData(null);
    if (replacement?.replaceBangumiId || replacement?.resourceId) navigate('/torrent', { replace: true, state: null });
  };

  const showOverlay = searchResult && !searchResult.error && searchResult.parsed_files;

  return (
    <>
      {replacement?.replaceBangumiId && <div className="mb-4 rounded-lg border border-primary p-4 text-sm">正在替换 {replacement.replaceName || replacement.replaceBangumiId} 的 RSS 文件。请上传 BD 种子，并逐集确认映射。</div>}
      {resourceLoading && <p role="status" className="mb-4 rounded-lg border border-border p-4 text-sm">正在解析资源种子…</p>}
      {/* Upload dropzone — always visible, dimmed when overlay is open */}
      <div className={showOverlay || resourceLoading ? 'opacity-40 pointer-events-none select-none' : ''}>
        <TorrentUpload onParse={handleParseTorrent} />
      </div>

      <section className="mt-8">
        <div className="mb-4 flex items-center justify-between gap-3">
          <h2 className="text-lg font-semibold">Torrent Collections</h2>
          <button type="button" onClick={() => void refreshCollections()} className="text-sm text-primary hover:underline cursor-pointer">Refresh</button>
        </div>
        {collectionsError && <p role="alert" className="mb-4 text-sm text-destructive">{collectionsError}</p>}
        {collectionsLoading ? (
          <p className="text-sm text-muted-foreground">Loading torrents...</p>
        ) : collections.length === 0 ? (
          <p className="text-sm text-muted-foreground">No torrents yet. Add a torrent to see it here.</p>
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
            {[...collections].reverse().map((item) => (
              <div key={item.info_hash}>
                <PosterCard
                  bangumiId={item.bangumi_ids?.[0]}
                  name={item.show_name || (item.bangumi_ids?.[0] ? `Bangumi ${item.bangumi_ids[0]}` : '未匹配 Bangumi')}
                  posterUrl={item.poster_url}
                  rating={item.bgm_rating}
                  tags={[
                    ...(item.encoding_group === 'ktnbytes' ? ['ktnbytes'] : []),
                    ...(item.video_codec?.toLowerCase() === 'av1' ? ['AV1'] : item.video_codec?.toLowerCase() === 'h265' ? ['H.265'] : []),
                  ]}
                />
                <FontinassStatus item={item} onRefresh={refreshCollections} />
              </div>
            ))}
          </div>
        )}
      </section>

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
              onClick={() => {
                setError(null);
                if (replacement?.resourceId) navigate('/torrent', { replace: true, state: null });
              }}
            >
              Try Again
            </button>
          </div>
        </div>
      )}

      {/* Parse error from server */}
      {searchResult?.error && (
        <div className="max-w-4xl mx-auto mt-4 glass-card rounded-xl p-4">
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm font-semibold text-destructive">Error</span>
            <button
              className="text-muted-foreground hover:text-foreground text-lg leading-none cursor-pointer"
              onClick={handleClosePreview}
            >
              &times;
            </button>
          </div>
          <pre className="text-xs text-muted-foreground">{searchResult.error}</pre>
        </div>
      )}

      {/* ── Preview Overlay ── */}
      {showOverlay && (
        <div className="fixed inset-0 z-40 flex">
          {/* Backdrop */}
          <div
            className="absolute inset-0 bg-black/50 backdrop-blur-sm"
            onClick={handleClosePreview}
          />

          {/* Slide-over panel */}
          <div className="relative ml-64 flex-1 bg-background overflow-y-auto custom-scrollbar shadow-2xl">
            <TorrentPreview
              replaceBangumiId={replacement?.replaceBangumiId}
              searchResult={searchResult}
              augmentedEpData={augmentedEpData}
              onEpisodeDataChange={setAugmentedEpData}
              onClose={handleClosePreview}
              onTorrentAdded={() => void refreshCollections()}
            />
          </div>
        </div>
      )}
    </>
  );
}
