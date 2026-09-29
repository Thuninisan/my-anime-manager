import { useEffect, useRef } from 'react';
import { Button } from '@/components/ui/button';
import { useUpdateCheck } from '@/hooks/useUpdateCheck';
import { showLoadingToast, updateToast } from '@/lib/toast';

/* Download icon — no emoji, pure SVG */
function IconDownload({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <polyline points="7 10 12 15 17 10" />
      <line x1="12" y1="15" x2="12" y2="3" />
    </svg>
  );
}

function IconRefresh({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="23 4 23 10 17 10" />
      <polyline points="1 20 1 14 7 14" />
      <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
    </svg>
  );
}

function IconCheckCircle({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
      <polyline points="22 4 12 14.01 9 11.01" />
    </svg>
  );
}

export default function UpdatePanel() {
  const { status, loading, applying, error, check, apply } = useUpdateCheck();
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Auto-check once on mount
  useEffect(() => {
    check();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleApply = async () => {
    const toastId = showLoadingToast('Updating... Application will restart shortly.');
    await apply();
    updateToast(toastId, 'Update triggered — waiting for application to come back...', 'loading');

    // Poll until the server is back
    let attempts = 0;
    pollRef.current = setInterval(async () => {
      attempts++;
      try {
        const res = await fetch('/api/version');
        if (res.ok) {
          if (pollRef.current) clearInterval(pollRef.current);
          updateToast(toastId, 'Update complete! Reloading page...', 'success');
          setTimeout(() => window.location.reload(), 1500);
        }
      } catch {
        // Server still restarting
        if (attempts > 120) {
          // 2 minutes timeout
          if (pollRef.current) clearInterval(pollRef.current);
          updateToast(toastId, 'Update may have completed — please reload the page manually.', 'error');
        }
      }
    }, 1000);
  };

  // Cleanup poll on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  return (
    <div className="space-y-6">
      {/* ── Current Version ── */}
      <div className="p-5 rounded-xl border border-border bg-card">
        <div className="flex items-center gap-3 mb-3">
          <div className="w-10 h-10 rounded-full bg-primary/10 flex items-center justify-center text-primary">
            <IconDownload size={20} />
          </div>
          <div>
            <h3 className="font-semibold text-sm">Application Version</h3>
            <p className="text-xs text-muted-foreground">Current installed version and update status</p>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4 mt-4">
          <div className="p-3 rounded-lg bg-muted/50">
            <p className="text-xs text-muted-foreground mb-1">Current Version</p>
            <p className="text-lg font-mono font-bold">
              {status?.current_version || '...'}
            </p>
            {status?.current_sha && (
              <p className="text-xs text-muted-foreground mt-0.5 font-mono">
                {status.current_sha}
              </p>
            )}
          </div>
          <div className="p-3 rounded-lg bg-muted/50">
            <p className="text-xs text-muted-foreground mb-1">Latest Available</p>
            {status?.update_available ? (
              <>
                <p className="text-lg font-mono font-bold text-amber-500">
                  {status.latest_tag || status.latest_sha || '...'}
                </p>
                {status.latest_sha && (
                  <p className="text-xs text-muted-foreground mt-0.5 font-mono">
                    {status.latest_sha}
                  </p>
                )}
              </>
            ) : (
              <p className="text-lg font-mono font-bold text-green-500">
                {status ? 'Up to date' : '...'}
              </p>
            )}
          </div>
        </div>
      </div>

      {/* ── Status Banner ── */}
      {status?.update_available && (
        <div className="p-4 rounded-xl border border-amber-500/30 bg-amber-500/10 flex items-center gap-3">
          <div className="w-8 h-8 rounded-full bg-amber-500/20 flex items-center justify-center text-amber-500 shrink-0">
            <IconRefresh size={18} />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-semibold">Update Available</p>
            <p className="text-xs text-muted-foreground mt-0.5">
              A new version is available. Click "Apply Update" to pull the latest code and restart.
            </p>
          </div>
        </div>
      )}

      {status && !status.update_available && !status.error && (
        <div className="p-4 rounded-xl border border-green-500/30 bg-green-500/10 flex items-center gap-3">
          <div className="w-8 h-8 rounded-full bg-green-500/20 flex items-center justify-center text-green-500 shrink-0">
            <IconCheckCircle size={18} />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-semibold">You are running the latest version</p>
            <p className="text-xs text-muted-foreground mt-0.5">
              No updates found. Check back later or click "Check for Updates" to verify.
            </p>
          </div>
        </div>
      )}

      {error && (
        <div className="p-4 rounded-xl border border-destructive/30 bg-destructive/10">
          <p className="text-sm text-destructive font-semibold">Error</p>
          <p className="text-xs text-muted-foreground mt-1">{error}</p>
        </div>
      )}

      {/* ── Actions ── */}
      <div className="flex gap-3">
        <Button
          variant="outline"
          onClick={check}
          disabled={loading}
          className="flex items-center gap-2"
        >
          <IconRefresh size={16} />
          {loading ? 'Checking...' : 'Check for Updates'}
        </Button>
        {status?.update_available && (
          <Button
            onClick={handleApply}
            disabled={applying}
            className="shadow-md shadow-primary/15 flex items-center gap-2"
          >
            <IconDownload size={16} />
            {applying ? 'Applying...' : 'Apply Update'}
          </Button>
        )}
      </div>

      {/* ── Info ── */}
      <div className="p-4 rounded-xl border border-border bg-muted/30">
        <p className="text-xs text-muted-foreground leading-relaxed">
          Updates are pulled directly from the GitHub repository. The application process will restart
          automatically after pulling the latest code. The Docker container itself stays running.
          Downtime is typically 10-60 seconds depending on whether frontend assets need rebuilding.
        </p>
      </div>
    </div>
  );
}
