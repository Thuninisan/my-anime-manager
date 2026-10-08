/** Custom hook for subtitle state management — extracted from MatchTable.tsx.
 *
 * Manages user-uploaded subtitles, stem-based matching against video
 * files, batch folder upload, and delete operations.
 */

import { useState, useMemo, useCallback, useRef } from 'react';
import { associateSubtitles, selectSubtitles, subtitlePeers, type SubtitlePreference, type SubtitleAssociations, type SubtitleFile } from '@/lib/subtitleMatching';
import type { MatchRow } from '@/types/matchTable';
import { BATCH_SUB_EXTENSIONS, extractEpisodeNumber } from '@/lib/matchUtils';
import { deleteSubtitle, uploadSubtitle } from '@/api/torrentApi';

export interface UseSubtitleMatchingReturn {
  uploadedSubtitles: { originalFilename: string; storedFilename: string }[];
  associations: SubtitleAssociations;
  preference: SubtitlePreference;
  changePreference: (value: SubtitlePreference) => void;
  setSelected: (id: string, target: string, selected: boolean) => void;
  subtitleFiles: SubtitleFile[];
  setAssociation: (id: string, target: string | null) => void;
  handleSubtitleUploaded: (originalFilename: string, storedFilename: string) => void;
  makeHandleSubtitleDeleted: (storedFilename: string) => () => Promise<void>;
  // Batch upload
  batchFolderRef: React.RefObject<HTMLInputElement | null>;
  batchProcessing: boolean;
  batchProgress: string;
  handleBatchFolderUpload: (e: React.ChangeEvent<HTMLInputElement>) => Promise<void>;
}

export function useSubtitleMatching(
  subtitles: string[],
  torrentName: string,
  tvRows: MatchRow[],
): UseSubtitleMatchingReturn {
  const [preference, setPreference] = useState<SubtitlePreference>('all');
  const [selection, setSelection] = useState<Record<string, boolean>>({});
  const changePreference = (value: SubtitlePreference) => { setPreference(value); setSelection({}); };
  const [manual, setManual] = useState<Record<string, string | null>>({});
  const setAssociation = (id: string, target: string | null) => {
    const file = subtitleFiles.find(file => file.id === id);
    const peers = file ? subtitlePeers(file, subtitleFiles) : [];
    setManual(prev => ({ ...prev, [id]: target, ...Object.fromEntries(peers.map(peer => [peer.id, target])) }));
  };
  const setSelected = (id: string, target: string, selected: boolean) => {
    const file = subtitleFiles.find(file => file.id === id);
    if (!file) return;
    const peers = subtitlePeers(file, subtitleFiles);
    // Selecting an unassociated candidate explicitly confirms its target.
    if (selected) setAssociation(id, target);
    setSelection(prev => ({ ...prev, ...Object.fromEntries(peers.map(peer => [peer.id, selected])) }));
  };
  // ── Uploaded subtitles state ──
  const [uploadedSubtitles, setUploadedSubtitles] = useState<
    { originalFilename: string; storedFilename: string }[]
  >([]);

  const subtitleFiles = useMemo<SubtitleFile[]>(() => [
    ...subtitles.map(path => ({ id: `torrent:${path}`, name: path.split('/').pop() || path, path, source: 'torrent' as const })),
    ...uploadedSubtitles.map(u => ({ id: `upload:${u.storedFilename}`, name: u.originalFilename, path: u.storedFilename, source: 'upload' as const })),
  ], [subtitles, uploadedSubtitles]);
  const associations = useMemo(() => selectSubtitles(associateSubtitles(tvRows, subtitleFiles, manual), preference, selection), [tvRows, subtitleFiles, manual, preference, selection]);
  const handleSubtitleUploaded = useCallback(
    (originalFilename: string, storedFilename: string) => {
      setUploadedSubtitles((prev) => [...prev, { originalFilename, storedFilename }]);
    },
    [],
  );

  const makeHandleSubtitleDeleted = useCallback(
    (storedFilename: string) => async () => {
      await deleteSubtitle(torrentName, storedFilename);
      setUploadedSubtitles((prev) => prev.filter((u) => u.storedFilename !== storedFilename));
    },
    [torrentName],
  );

  // ── Batch folder upload ──
  const batchFolderRef = useRef<HTMLInputElement>(null);
  const [batchProcessing, setBatchProcessing] = useState(false);
  const [batchProgress, setBatchProgress] = useState('');

  const handleBatchFolderUpload = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;

    setBatchProcessing(true);
    setBatchProgress('');

    const subFiles: { file: File; relativePath: string }[] = [];
    for (let i = 0; i < files.length; i++) {
      const f = files[i];
      const ext = '.' + f.name.split('.').pop()?.toLowerCase();
      if (BATCH_SUB_EXTENSIONS.has(ext)) {
        const relPath = (f as any).webkitRelativePath || f.name;
        subFiles.push({ file: f, relativePath: relPath });
      }
    }

    if (subFiles.length === 0) {
      setBatchProgress('文件夹中未找到字幕文件');
      setBatchProcessing(false);
      if (batchFolderRef.current) batchFolderRef.current.value = '';
      return;
    }

    const epToRow = new Map<number, typeof tvRows[0]>();
    const ambiguous = new Set<number>();
    for (const row of tvRows) {
      const ep = row.mapping.parsed.episode_number;
      if (ep != null && epToRow.has(ep)) ambiguous.add(ep);
      if (ep != null && ep > 0 && !epToRow.has(ep)) {
        epToRow.set(ep, row);
      }
    }

    let matched = 0;
    let skipped = 0;
    const errors: string[] = [];

    for (const { file, relativePath } of subFiles) {
      const epNum = extractEpisodeNumber(relativePath);
      if (epNum === null) {
        skipped++;
        continue;
      }

      const targetRow = epToRow.get(epNum);
      if (!targetRow || ambiguous.has(epNum)) {
        skipped++;
        continue;
      }

      const videoStem = targetRow.file_name.replace(/\.[^.]+$/, '');

      try {
        const result = await uploadSubtitle(file, torrentName, videoStem);
        setUploadedSubtitles((prev) => [...prev, {
          originalFilename: file.name,
          storedFilename: result.filename,
        }]);
        setManual(prev => ({ ...prev, [`upload:${result.filename}`]: targetRow.torrent_path }));
        matched++;
      } catch (err: any) {
        errors.push(`${file.name}: ${err.message}`);
      }
    }

    let msg = `匹配 ${matched} 个字幕`;
    if (skipped > 0) msg += `，跳过 ${skipped} 个`;
    if (errors.length > 0) msg += `，${errors.length} 个失败`;
    setBatchProgress(msg);

    setBatchProcessing(false);
    if (batchFolderRef.current) batchFolderRef.current.value = '';
  }, [tvRows, torrentName]);

  return {
    uploadedSubtitles,
    associations, subtitleFiles, setAssociation, preference, changePreference, setSelected,
    handleSubtitleUploaded,
    makeHandleSubtitleDeleted,
    batchFolderRef,
    batchProcessing,
    batchProgress,
    handleBatchFolderUpload,
  };
}
