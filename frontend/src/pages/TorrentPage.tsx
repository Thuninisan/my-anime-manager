import { usePreviewFlow } from '@/hooks/usePreviewFlow';
import TorrentUpload from '@/components/TorrentUpload';
import PreviewDashboard from '@/components/PreviewDashboard';
import ProcessingResult from '@/components/ProcessingResult';

export default function TorrentPage() {
  const {
    state,
    previewData,
    confirmResult,
    error,
    uploadTorrent,
    confirmTorrent,
    reset,
  } = usePreviewFlow();

  const isPreviewState = state === 'preview' && previewData;

  return (
    <>
      {/* idle / uploading: show dropzone */}
      {(state === 'idle' || state === 'uploading') && (
        <TorrentUpload onUpload={uploadTorrent} uploading={state === 'uploading'} />
      )}

      {/* error */}
      {state === 'error' && (
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
            <p className="text-sm text-muted-foreground mb-6 whitespace-pre-wrap">{error || 'Unknown error'}</p>
            <button
              className="inline-flex items-center gap-2 rounded-lg bg-primary text-primary-foreground px-5 py-2.5 text-sm font-semibold hover:bg-primary/85 shadow-md shadow-primary/15 transition cursor-pointer"
              onClick={reset}
            >
              Try Again
            </button>
          </div>
        </div>
      )}

      {/* preview: cards + stats + footer */}
      {isPreviewState && (
        <PreviewDashboard data={previewData} onConfirm={confirmTorrent} onCancel={reset} />
      )}

      {/* confirming */}
      {state === 'confirming' && (
        <div className="glass-card rounded-xl border-2 border-dashed border-accent/40 p-16 flex flex-col items-center justify-center text-center space-y-4">
          <div className="w-16 h-16 bg-accent/15 rounded-full flex items-center justify-center">
            <div className="w-8 h-8 border-[3px] border-accent/30 border-t-accent rounded-full animate-spin" />
          </div>
          <div>
            <h2 className="text-xl font-semibold text-primary">Processing...</h2>
            <p className="text-sm text-muted-foreground mt-1 max-w-md">
              Generating NFO files, downloading images, renaming files in qBittorrent...
            </p>
          </div>
        </div>
      )}

      {/* done */}
      {state === 'done' && confirmResult && (
        <ProcessingResult result={confirmResult} onStartOver={reset} />
      )}
    </>
  );
}
