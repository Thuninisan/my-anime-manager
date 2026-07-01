import { useState, useRef, useCallback, type DragEvent } from 'react';

interface Props {
  onParse: (file: File) => Promise<void>;
}

/* ======== Icons (SVG, no emoji) ======== */

function CloudUploadIcon() {
  return (
    <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9Z" />
      <path d="M12 13v6" />
      <path d="M9 16l3-3 3 3" />
    </svg>
  );
}

function FileIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
    </svg>
  );
}

function AddIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <line x1="12" y1="5" x2="12" y2="19" />
      <line x1="5" y1="12" x2="19" y2="12" />
    </svg>
  );
}

function LinkIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
      <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
    </svg>
  );
}

/* ======== Component ======== */

export default function TorrentUpload({ onParse }: Props) {
  const [dragOver, setDragOver] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [showMagnetInput, setShowMagnetInput] = useState(false);
  const [magnetLink, setMagnetLink] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  /* ── File handling ── */

  const handleFile = useCallback((file: File) => {
    if (!file.name.endsWith('.torrent')) {
      alert('Please select a .torrent file.');
      return;
    }
    setSelectedFile(file);
  }, []);

  const handleDrop = useCallback((e: DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files[0];
    if (file) handleFile(file);
  }, [handleFile]);

  const handleChange = useCallback(() => {
    const file = inputRef.current?.files?.[0];
    if (file) handleFile(file);
  }, [handleFile]);

  /* ── Parse & search ── */

  const handleParse = useCallback(async () => {
    if (!selectedFile || analyzing) return;
    setAnalyzing(true);
    try {
      await onParse(selectedFile);
    } catch {
      // error handled by parent
    } finally {
      setAnalyzing(false);
      setSelectedFile(null);
      if (inputRef.current) inputRef.current.value = '';
    }
  }, [selectedFile, analyzing, onParse]);

  const handleClear = useCallback(() => {
    setSelectedFile(null);
    if (inputRef.current) inputRef.current.value = '';
  }, []);

  /* ── Drag handlers for the whole dropzone ── */

  const dropZoneHandlers = {
    onDragOver: (e: DragEvent) => { e.preventDefault(); setDragOver(true); },
    onDragLeave: () => setDragOver(false),
    onDrop: handleDrop,
  };

  /* ── Magnet placeholder ── */

  const handleMagnetSubmit = () => {
    if (!magnetLink.trim()) return;
    // Magnet link parsing requires downloading torrent metadata first.
    // For now, alert the user that this feature is coming.
    alert('Magnet link support coming soon.\n\nPaste the magnet link into qBittorrent directly, then use the Watch Directory or manual .torrent upload.');
    setMagnetLink('');
    setShowMagnetInput(false);
  };

  /* ================================================================== */
  /*                              RENDER                                 */
  /* ================================================================== */

  return (
    <>
      {/* ── Analyzing overlay ── */}
      {analyzing && (
        <section className="bg-surface-light dark:bg-surface-dark rounded-2xl p-6 border-2 border-dashed border-[#f09199]/40 flex items-center justify-center gap-4 sakura-shadow">
          <div className="w-10 h-10 border-[3px] border-[#f09199]/25 border-t-[#f09199] rounded-full animate-spin" />
          <div>
            <h3 className="font-semibold text-base text-foreground">Analyzing torrent...</h3>
            <p className="text-sm text-muted-foreground">Searching TMDB + Bangumi, building preview</p>
          </div>
        </section>
      )}

      {/* ── File selected ── */}
      {!analyzing && selectedFile && (
        <section className="bg-surface-light dark:bg-surface-dark rounded-2xl p-6 border-2 border-dashed border-[#f09199]/40 flex items-center justify-between sakura-shadow">
          <div className="flex items-center gap-4">
            <div className="w-12 h-12 bg-[#f09199]/10 rounded-xl flex items-center justify-center text-[#f09199]">
              <FileIcon />
            </div>
            <div>
              <h3 className="font-semibold text-base text-foreground">{selectedFile.name}</h3>
              <p className="text-sm text-muted-foreground">
                {(selectedFile.size / 1024).toFixed(1)} KB &middot; Ready to analyze
              </p>
            </div>
          </div>
          <div className="flex gap-2">
            <button
              className="bg-[#f09199] text-white px-5 py-2 rounded-lg font-semibold text-sm flex items-center gap-1.5 shadow-md hover:brightness-110 transition-all cursor-pointer"
              onClick={handleParse}
            >
              <AddIcon />
              Analyze
            </button>
            <button
              className="bg-surface border border-border text-muted-foreground px-4 py-2 rounded-lg font-medium text-sm flex items-center gap-1.5 hover:bg-muted transition-colors cursor-pointer"
              onClick={handleClear}
            >
              Clear
            </button>
          </div>
        </section>
      )}

      {/* ── Idle dropzone ── */}
      {!analyzing && !selectedFile && (
        <section
          id="dropzone"
          className={`
            bg-surface-light dark:bg-surface-dark rounded-2xl p-6 border-2 border-dashed flex items-center justify-between
            sakura-shadow transition-all group cursor-pointer
            ${dragOver
              ? 'border-[#f09199]/50 bg-[#f09199]/5 scale-[1.01]'
              : 'border-border/50 hover:border-[#f09199]/30'
            }
          `}
          {...dropZoneHandlers}
          onClick={() => inputRef.current?.click()}
        >
          {/* Hidden file input */}
          <input
            ref={inputRef}
            type="file"
            accept=".torrent"
            onChange={handleChange}
            className="hidden"
          />

          {/* Left: icon + text */}
          <div className="flex items-center gap-5">
            <div className="w-16 h-16 bg-[#f09199]/10 rounded-xl flex items-center justify-center text-[#f09199] group-hover:scale-105 transition-transform duration-300">
              <CloudUploadIcon />
            </div>
            <div className="text-left">
              <h3 className="font-semibold text-lg text-foreground">Import Torrents</h3>
              <p className="text-sm text-muted-foreground">Drop files here or paste a magnet link to start processing</p>
            </div>
          </div>

          {/* Right: action buttons */}
          <div className="flex gap-2" onClick={(e) => e.stopPropagation()}>
            <button
              className="bg-[#f09199] text-white px-5 py-2 rounded-lg font-semibold text-sm flex items-center gap-1.5 shadow-md hover:brightness-110 transition-all cursor-pointer"
              onClick={() => inputRef.current?.click()}
            >
              <AddIcon />
              Browse Files
            </button>
            <button
              className="bg-surface border border-border text-muted-foreground px-4 py-2 rounded-lg font-medium text-sm flex items-center gap-1.5 hover:bg-muted transition-colors cursor-pointer"
              onClick={() => setShowMagnetInput(true)}
            >
              <LinkIcon />
              Magnet
            </button>
          </div>
        </section>
      )}

      {/* ── Magnet input dialog ── */}
      {showMagnetInput && (
        <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center" onClick={() => setShowMagnetInput(false)}>
          <div
            className="bg-surface-light dark:bg-surface-dark rounded-2xl p-6 shadow-xl max-w-[520px] w-full mx-4 space-y-4"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="font-semibold text-lg text-foreground">Add Magnet Link</h3>
            <input
              type="text"
              className="w-full bg-muted/50 border border-border rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#f09199]/50"
              placeholder="magnet:?xt=urn:btih:..."
              value={magnetLink}
              onChange={(e) => setMagnetLink(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') handleMagnetSubmit(); }}
            />
            <div className="flex justify-end gap-2">
              <button
                className="px-4 py-2 rounded-lg text-sm text-muted-foreground hover:bg-muted transition-colors cursor-pointer"
                onClick={() => { setShowMagnetInput(false); setMagnetLink(''); }}
              >
                Cancel
              </button>
              <button
                className="bg-[#f09199] text-white px-5 py-2 rounded-lg font-semibold text-sm hover:brightness-110 transition-all cursor-pointer"
                onClick={handleMagnetSubmit}
              >
                Add
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
