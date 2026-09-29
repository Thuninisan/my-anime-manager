/** Custom hook for subtitle state management — extracted from MatchTable.tsx.
 *
 * Manages user-uploaded subtitles, stem-based matching against video
 * files, batch folder upload, and delete operations.
 */

import { useState, useMemo, useCallback, useRef } from 'react';
import type { MatchRow } from '@/types/matchTable';
import { BATCH_SUB_EXTENSIONS, extractEpisodeNumber } from '@/lib/matchUtils';
import { deleteSubtitle, uploadSubtitle } from '@/api/torrentApi';

export interface UseSubtitleMatchingReturn {
  uploadedSubtitles: { originalFilename: string; storedFilename: string }[];
  combinedSubtitles: string[];
  hasMatchingSubtitle: (videoFileName: string) => boolean;
  isUploadedMatch: (videoFileName: string) => boolean;
  getUploadedStoredFilename: (videoFileName: string) => string | null;
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
  tvRows: (MatchRow & { _idx: number })[],
): UseSubtitleMatchingReturn {
  // ── Uploaded subtitles state ──
  const [uploadedSubtitles, setUploadedSubtitles] = useState<
    { originalFilename: string; storedFilename: string }[]
  >([]);

  const combinedSubtitles = useMemo(
    () => [...subtitles, ...uploadedSubtitles.map((u) => u.storedFilename)],
    [subtitles, uploadedSubtitles],
  );

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

  // ── Stem-based matching helpers ──
  const hasMatchingSubtitle = (videoFileName: string): boolean => {
    const videoStem = videoFileName.replace(/\.[^.]+$/, '').toLowerCase();
    return combinedSubtitles.some(
      (sub) => sub.replace(/\.[^.]+$/, '').toLowerCase() === videoStem,
    );
  };

  const isUploadedMatch = (videoFileName: string): boolean => {
    const videoStem = videoFileName.replace(/\.[^.]+$/, '').toLowerCase();
    return uploadedSubtitles.some(
      (u) => u.storedFilename.replace(/\.[^.]+$/, '').toLowerCase() === videoStem,
    );
  };

  const getUploadedStoredFilename = (videoFileName: string): string | null => {
    const videoStem = videoFileName.replace(/\.[^.]+$/, '').toLowerCase();
    const match = uploadedSubtitles.find(
      (u) => u.storedFilename.replace(/\.[^.]+$/, '').toLowerCase() === videoStem,
    );
    return match?.storedFilename ?? null;
  };

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
    for (const row of tvRows) {
      const ep = row.src_episode;
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
      if (!targetRow) {
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
    combinedSubtitles,
    hasMatchingSubtitle,
    isUploadedMatch,
    getUploadedStoredFilename,
    handleSubtitleUploaded,
    makeHandleSubtitleDeleted,
    batchFolderRef,
    batchProcessing,
    batchProgress,
    handleBatchFolderUpload,
  };
}
