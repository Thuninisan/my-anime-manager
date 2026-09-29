import { useState } from 'react';
import type { SubscriptionOut } from '@/types/preview';

interface Props {
  open: boolean;
  subscription: SubscriptionOut | null;
  onClose: () => void;
  onConfirm: (bangumiId: number, deleteFiles: boolean) => void;
}

export default function UnsubscribeDialog({ open, subscription, onClose, onConfirm }: Props) {
  const [deleteFiles, setDeleteFiles] = useState(false);

  if (!open || !subscription) return null;

  const handleConfirm = () => {
    onConfirm(subscription.bangumi_id, deleteFiles);
    setDeleteFiles(false);
  };

  const handleClose = () => {
    setDeleteFiles(false);
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-sm">
      <div className="bg-card w-full max-w-md rounded-xl shadow-2xl overflow-hidden border border-border">
        {/* Header */}
        <div className="px-5 py-4 border-b border-border">
          <h3 className="text-base font-semibold text-foreground">Unsubscribe</h3>
          <p className="text-sm text-muted-foreground mt-1 truncate">{subscription.name}</p>
        </div>

        {/* Body */}
        <div className="px-5 py-4 space-y-3">
          <p className="text-sm text-foreground">
            Are you sure you want to remove this subscription?
          </p>

          {/* Checkbox */}
          <label className="flex items-start gap-3 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={deleteFiles}
              onChange={(e) => setDeleteFiles(e.target.checked)}
              className="mt-0.5 h-4 w-4 rounded border-border text-destructive focus:ring-destructive/30 cursor-pointer accent-destructive"
            />
            <div>
              <span className="text-sm font-medium text-foreground">Also delete downloaded files</span>
              {deleteFiles && (
                <p className="text-xs text-destructive mt-1">
                  This will remove all related torrents from qBittorrent and clear download history. This action cannot be undone.
                </p>
              )}
            </div>
          </label>
        </div>

        {/* Footer */}
        <div className="px-5 py-3 border-t border-border bg-muted/20 flex justify-end gap-3">
          <button
            className="px-4 py-2 rounded-lg text-sm font-medium border border-border text-muted-foreground hover:bg-muted transition-colors cursor-pointer"
            onClick={handleClose}
          >
            Cancel
          </button>
          <button
            className={`px-4 py-2 rounded-lg text-sm font-semibold transition-colors cursor-pointer ${
              deleteFiles
                ? 'bg-destructive text-destructive-foreground hover:brightness-110'
                : 'bg-primary text-primary-foreground hover:brightness-110'
            }`}
            onClick={handleConfirm}
          >
            {deleteFiles ? 'Delete All' : 'Unsubscribe'}
          </button>
        </div>
      </div>
    </div>
  );
}
