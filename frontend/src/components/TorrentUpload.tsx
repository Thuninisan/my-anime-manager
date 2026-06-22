import { useState, useRef, useCallback, type DragEvent } from 'react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

interface Props { onUpload: (file: File) => void; uploading: boolean; }

function UploadIcon({ className }: { className?: string }) {
  return (
    <svg className={cn("size-12", className)} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <polyline points="17 8 12 3 7 8" />
      <line x1="12" y1="3" x2="12" y2="15" />
    </svg>
  );
}

function FileIcon({ className }: { className?: string }) {
  return (
    <svg className={cn("size-6", className)} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
    </svg>
  );
}

export default function TorrentUpload({ onUpload, uploading }: Props) {
  const [dragOver, setDragOver] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

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

  const handleUpload = () => {
    if (selectedFile && !uploading) onUpload(selectedFile);
  };

  const handleClear = () => {
    setSelectedFile(null);
    if (inputRef.current) inputRef.current.value = '';
  };

  /* ── Uploading state ── */
  if (uploading) {
    return (
      <section className="glass-card rounded-xl p-12 border-2 border-dashed border-accent/40 flex flex-col items-center justify-center text-center space-y-6">
        <div className="w-16 h-16 bg-accent/15 rounded-full flex items-center justify-center">
          <div className="w-8 h-8 border-[3px] border-accent/30 border-t-accent rounded-full animate-spin" />
        </div>
        <div>
          <h3 className="text-xl font-semibold text-primary leading-tight">Analyzing torrent...</h3>
          <p className="text-base text-muted-foreground max-w-md mx-auto mt-1">
            Searching TMDB, Bangumi, and building preview. This may take a moment.
          </p>
        </div>
      </section>
    );
  }

  /* ── File selected state ── */
  if (selectedFile) {
    return (
      <section className="glass-card rounded-xl p-12 border-2 border-dashed border-accent/60 bg-accent/5 flex flex-col items-center justify-center text-center space-y-6">
        <div className="w-16 h-16 bg-accent/10 rounded-full flex items-center justify-center">
          <FileIcon className="text-accent" />
        </div>
        <div>
          <h3 className="text-lg font-semibold text-foreground">{selectedFile.name}</h3>
          <p className="text-base text-muted-foreground mt-1">
            {(selectedFile.size / 1024).toFixed(1)} KB &middot; Ready to preview
          </p>
        </div>
        <div className="flex gap-3">
          <Button onClick={handleUpload} className="shadow-md shadow-primary/15 rounded-lg">
            Upload &amp; Preview
          </Button>
          <Button variant="outline" onClick={handleClear}>
            Clear
          </Button>
        </div>
      </section>
    );
  }

  /* ── Idle / dropzone state ── */
  return (
    <section
      className={cn(
        "glass-card rounded-xl p-12 border-2 border-dashed flex flex-col items-center justify-center text-center space-y-6 transition-all cursor-pointer group",
        dragOver
          ? "border-accent bg-accent/10 scale-[1.01]"
          : "border-accent/30 hover:border-accent/60 hover:bg-accent/5",
      )}
      onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
      onDragLeave={() => setDragOver(false)}
      onDrop={handleDrop}
      onClick={() => inputRef.current?.click()}
    >
      <input
        ref={inputRef}
        type="file"
        accept=".torrent"
        onChange={handleChange}
        className="hidden"
      />

      {/* Icon */}
      <div className={cn(
        "w-16 h-16 bg-accent/10 rounded-full flex items-center justify-center transition-transform",
        dragOver && "scale-110",
      )}>
        <UploadIcon className={cn(
          "text-accent transition-colors",
          dragOver && "text-primary",
        )} />
      </div>

      {/* Text */}
      <div>
        <h3 className="text-xl font-semibold text-primary leading-tight">
          Upload .torrent Files
        </h3>
        <p className="text-base text-muted-foreground max-w-md mx-auto mt-1">
          Drag and drop your anime torrents here to begin intelligent metadata mapping and file organization.
        </p>
      </div>

      {/* Actions */}
      <div className="flex gap-3">
        <Button
          className="shadow-md shadow-primary/15 rounded-lg"
          onClick={(e) => {
            e.stopPropagation();
            inputRef.current?.click();
          }}
        >
          <svg className="size-4 mr-1.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M5 12h14" />
            <path d="M12 5v14" />
          </svg>
          Browse Files
        </Button>
        <Button variant="outline" onClick={(e) => e.stopPropagation()}>
          <svg className="size-4 mr-1.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
            <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
          </svg>
          Add Magnet
        </Button>
      </div>
    </section>
  );
}
