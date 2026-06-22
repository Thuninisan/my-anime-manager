import { useState, useEffect, useMemo } from "react";
import type { EpisodeBlock, TmdbSeasonData, BangumiSeasonData } from "../../types/preview";
import {
  SheetRoot,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
  SheetBody,
  SheetFooter,
  SheetClose,
} from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  filename: string;
  episode: EpisodeBlock;
  seasonOptions: [string, TmdbSeasonData][];
  bangumiData: Record<string, BangumiSeasonData>;
  onSave: (filename: string, updated: Partial<EpisodeBlock>) => void;
}

const EXTRA_KEY_BASE = 900;

function sanitizeDirName(name: string): string {
  return name.replace(/[<>:"/\\|?*]/g, '');
}

export default function EpisodeEditSheet({
  open,
  onOpenChange,
  filename,
  episode,
  seasonOptions,
  bangumiData,
  onSave,
}: Props) {
  const [tmdbSeason, setTmdbSeason] = useState(episode.tmdb_season);
  const [episodeNumber, setEpisodeNumber] = useState(episode.episode_number);
  const [bangumiEpId, setBangumiEpId] = useState(
    episode.bangumi_ep_id?.toString() || ""
  );
  const [bangumiSubjectName, setBangumiSubjectName] = useState(
    episode.bangumi_subject_name
  );

  // Sync local state when episode prop changes (e.g. after save + re-edit)
  useEffect(() => {
    setTmdbSeason(episode.tmdb_season);
    setEpisodeNumber(episode.episode_number);
    setBangumiEpId(episode.bangumi_ep_id?.toString() || "");
    setBangumiSubjectName(episode.bangumi_subject_name);
  }, [episode]);

  // Compute effective season_number from selected bangumi_subject_name
  const effectiveSeasonNumber = useMemo(() => {
    if (!bangumiSubjectName) return episode.season_number;
    for (const [key, data] of Object.entries(bangumiData)) {
      if (data.name === bangumiSubjectName) return parseInt(key, 10);
    }
    return episode.season_number;
  }, [bangumiSubjectName, bangumiData, episode.season_number]);

  // Live preview of newPath based on current selections
  const previewNewPath = useMemo(() => {
    if (!bangumiSubjectName) return episode.newPath;

    const oldNewPath = episode.newPath || '';
    const seasonMarker = `/Season ${episode.season_number}/`;
    const seasonIdx = oldNewPath.indexOf(seasonMarker);
    const showDirName = seasonIdx >= 0
      ? oldNewPath.substring(0, seasonIdx)
      : '';
    const ext = episode.oldPath.substring(episode.oldPath.lastIndexOf('.'));
    if (effectiveSeasonNumber === 0 || effectiveSeasonNumber >= EXTRA_KEY_BASE) {
      const epLabel = effectiveSeasonNumber === 0
        ? `S00E${String(episodeNumber).padStart(2, '0')}`
        : `E${String(episodeNumber).padStart(2, '0')}`;
      return `${showDirName}/Specials/${sanitizeDirName(bangumiSubjectName)} ${epLabel}${ext}`;
    }
    return `${showDirName}/Season ${effectiveSeasonNumber}/${sanitizeDirName(bangumiSubjectName)} ${String(episodeNumber).padStart(2, '0')}${ext}`;
  }, [bangumiSubjectName, episodeNumber, effectiveSeasonNumber, episode]);

  const handleSave = () => {
    onSave(filename, {
      tmdb_season: tmdbSeason,
      episode_number: episodeNumber,
      bangumi_ep_id: bangumiEpId ? parseInt(bangumiEpId, 10) : null,
      bangumi_subject_name: bangumiSubjectName,
      season_number: effectiveSeasonNumber,
    });
    onOpenChange(false);
  };

  const seasonEpisodes =
    seasonOptions.find(
      ([sk]) => parseInt(sk, 10) === tmdbSeason
    )?.[1]?.episodes || {};

  // Bangumi subject name options from bangumi_data
  const subjectNameOptions = useMemo(() => {
    const opts: { label: string; value: string }[] = [];
    for (const [key, data] of Object.entries(bangumiData)) {
      const sn = parseInt(key, 10);
      const prefix = sn >= EXTRA_KEY_BASE && data.kind ? `[${data.kind}] ` : `[${key}] `;
      opts.push({ label: `${prefix}${data.name}`, value: data.name });
    }
    return opts;
  }, [bangumiData]);

  // Bangumi episode options from bangumi_data for the selected subject
  const bangumiEpisodeOptions = useMemo(() => {
    const opts: { label: string; value: string }[] = [];
    // Find the bangumi_data entry matching the selected subject name
    for (const [, data] of Object.entries(bangumiData)) {
      if (data.name === bangumiSubjectName) {
        for (const ep of data.episodes) {
          opts.push({
            label: `${ep.sort} — ${ep.id}`,
            value: String(ep.id),
          });
        }
        break;
      }
    }
    return opts;
  }, [bangumiData, bangumiSubjectName]);

  // When subject name changes, if current episode ID isn't in new options, clear it
  useEffect(() => {
    if (bangumiEpisodeOptions.length > 0 && bangumiEpId) {
      const exists = bangumiEpisodeOptions.some(o => o.value === bangumiEpId);
      if (!exists) setBangumiEpId("");
    }
  }, [bangumiEpisodeOptions, bangumiEpId]);

  return (
    <SheetRoot open={open} onOpenChange={onOpenChange}>
      <SheetContent>
        <SheetHeader>
          <div>
            <SheetTitle>编辑集映射</SheetTitle>
            <SheetDescription className="mt-1">
              {filename}
            </SheetDescription>
          </div>
          <SheetClose className="rounded-lg p-1.5 text-muted-foreground hover:text-foreground hover:bg-muted transition cursor-pointer">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </SheetClose>
        </SheetHeader>

        <SheetBody className="space-y-5">
          {/* TMDB Season */}
          <div className="space-y-2">
            <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
              TMDB 季
            </label>
            <select
              className="w-full bg-muted border border-border rounded-lg px-3 py-2 text-sm cursor-pointer focus:outline-none focus:border-ring focus:ring-2 focus:ring-ring/30 transition"
              value={tmdbSeason}
              onChange={(e) => {
                const newSeason = parseInt(e.target.value, 10);
                setTmdbSeason(newSeason);
                // Auto-select first episode of the new season
                const firstEpKey = Object.keys(
                  seasonOptions.find(
                    ([sk]) => parseInt(sk, 10) === newSeason
                  )?.[1]?.episodes || {}
                )[0];
                if (firstEpKey) {
                  setEpisodeNumber(parseInt(firstEpKey, 10));
                }
              }}
            >
              {seasonOptions.map(([sk, sd]) => (
                <option key={sk} value={sk}>
                  S{sk.padStart(2, "0")} — {sd.name}
                </option>
              ))}
            </select>
          </div>

          {/* TMDB Episode */}
          <div className="space-y-2">
            <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
              TMDB 集
            </label>
            <select
              className="w-full bg-muted border border-border rounded-lg px-3 py-2 text-sm cursor-pointer focus:outline-none focus:border-ring focus:ring-2 focus:ring-ring/30 transition"
              value={episodeNumber}
              onChange={(e) => setEpisodeNumber(parseInt(e.target.value, 10))}
            >
              {Object.entries(seasonEpisodes).map(([ek, ed]) => (
                <option key={ek} value={ek}>
                  E{ek.padStart(2, "0")} — {ed.name}
                </option>
              ))}
            </select>
          </div>

          {/* Divider */}
          <div className="border-t border-border" />

          {/* Bangumi Subject Name */}
          <div className="space-y-2">
            <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
              Bangumi 条目名称
            </label>
            <select
              className="w-full bg-muted border border-border rounded-lg px-3 py-2 text-sm cursor-pointer focus:outline-none focus:border-ring focus:ring-2 focus:ring-ring/30 transition"
              value={bangumiSubjectName}
              onChange={(e) => setBangumiSubjectName(e.target.value)}
            >
              <option value="">—</option>
              {subjectNameOptions.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>

          {/* Bangumi Episode ID */}
          <div className="space-y-2">
            <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
              Bangumi 集 ID
            </label>
            <select
              className="w-full bg-muted border border-border rounded-lg px-3 py-2 text-sm cursor-pointer focus:outline-none focus:border-ring focus:ring-2 focus:ring-ring/30 transition"
              value={bangumiEpId}
              onChange={(e) => setBangumiEpId(e.target.value)}
            >
              <option value="">—</option>
              {bangumiEpisodeOptions.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>

          {/* Divider */}
          <div className="border-t border-border" />

          {/* Read-only info */}
          <div className="space-y-3">
            <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
              只读信息
            </label>

            <div className="space-y-1">
              <span className="text-xs text-muted-foreground">季序号 (season_number)</span>
              <p className={`text-sm font-mono bg-muted/50 px-2 py-1 rounded ${effectiveSeasonNumber !== episode.season_number ? 'text-warning' : ''}`}>
                {effectiveSeasonNumber}
              </p>
            </div>

            <div className="space-y-1">
              <span className="text-xs text-muted-foreground">原路径</span>
              <p
                className="text-xs font-mono bg-muted/50 px-2 py-1 rounded break-all"
                title={episode.oldPath}
              >
                {episode.oldPath}
              </p>
            </div>

            <div className="space-y-1">
              <span className="text-xs text-muted-foreground">新路径</span>
              <p
                className="text-xs font-mono bg-muted/50 px-2 py-1 rounded break-all text-success"
                title={previewNewPath}
              >
                {previewNewPath}
              </p>
            </div>

            {episode.tmdb && (
              <div className="space-y-1">
                <span className="text-xs text-muted-foreground">TMDB 集信息</span>
                <div className="text-xs bg-muted/50 px-2 py-1.5 rounded space-y-0.5">
                  <p><span className="text-muted-foreground">名称:</span> {episode.tmdb.name}</p>
                  <p><span className="text-muted-foreground">播出日:</span> {episode.tmdb.air_date || "N/A"}</p>
                  <p><span className="text-muted-foreground">时长:</span> {episode.tmdb.runtime} min</p>
                  <p><span className="text-muted-foreground">TMDB ID:</span> {episode.tmdb.id}</p>
                </div>
              </div>
            )}
          </div>
        </SheetBody>

        <SheetFooter>
          <SheetClose className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-background px-3 py-1.5 text-sm font-medium hover:bg-muted transition cursor-pointer">
            取消
          </SheetClose>
          <Button onClick={handleSave}>保存更改</Button>
        </SheetFooter>
      </SheetContent>
    </SheetRoot>
  );
}
