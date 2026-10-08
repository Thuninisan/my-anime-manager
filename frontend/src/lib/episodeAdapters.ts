/** Public preview projection and legacy matcher coordinate boundary. */
import type { EpisodeCatalog, CatalogSeason, EpisodeMapping, TmdbEpisodeRef, ParsedEpisodeRef, BangumiCatalogEpisode, EpisodeMatchSource } from '@/types/episode';
import type { ParsedFile } from '@/types/matchTable';
import type { TorrentPreviewResponse } from '@/types/preview';

const object = (value: unknown): Record<string, unknown> =>
  value != null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
const list = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
const num = (value: unknown): number | null => typeof value === 'number' && Number.isFinite(value) ? value : null;
const str = (value: unknown): string => typeof value === 'string' ? value : '';
const emptyRef = (): TmdbEpisodeRef => ({ series_id: null, episode_id: null, season_number: null, episode_number: null });

export function normalizeEpisodeCatalog(value: unknown): EpisodeCatalog {
  const raw = object(value);
  const result: EpisodeCatalog = { tmdb: {}, bangumi: {}, tvdb: {} };
  const titles: Record<string, string> = { ...object(raw.tmdb_series_titles) } as Record<string, string>;
  for (const provider of ['tmdb', 'tvdb'] as const) {
    for (const [id, value] of Object.entries(object(raw[provider]))) {
      const entry = object(value);

      const seasons: Record<string, CatalogSeason> = {};
      for (const [season, data] of Object.entries(provider === 'tvdb' ? object(entry.seasons) : entry)) {
        const sd = object(data);
        if (!Array.isArray(sd.episodes)) continue;
        seasons[season] = { name: str(sd.name), episodes: sd.episodes.flatMap(value => {
          const ep = object(value);
          const episodeNumber = num(ep.episode_number);
          if (episodeNumber == null) return [];
          const normalized = {
            series_id: Number(id), episode_id: num(ep.episode_id),
            season_number: Number(season), episode_number: episodeNumber,
            episode_absolute: num(ep.episode_absolute),
            name: str(ep.name), name_cn: str(ep.name_cn),
          };
          return [normalized];
        }) };
      }
      if (provider === 'tmdb') result.tmdb[id] = seasons;
      else result.tvdb[id] = { name: str(entry.name), seasons };
    }
  }
  for (const [id, value] of Object.entries(object(raw.bangumi))) {
    const entry = object(value);
    result.bangumi[id] = { name: str(entry.name), episodes: list(entry.episodes).flatMap(value => {
      const ep = object(value);
      const episodeId = num(ep.episode_id);
      if (episodeId == null) return [];
      const normalized = { subject_id: Number(id), episode_id: episodeId,
        episode_number: num(ep.episode_number),
        // Do not substitute ep for sort; preserve raw sort if supplied, including null.
        episode_absolute: num(ep.episode_absolute),
        name: str(ep.name), name_cn: str(ep.name_cn),
        matching_absolute: num(ep.matching_absolute) };
      return [normalized];
    }) };
  }
  result.tmdb_series_titles = titles;
  return result;
}

export function normalizeTorrentPreview(value: unknown): TorrentPreviewResponse {
  const raw = object(value);
  const parsedFiles = (value: unknown): ParsedFile[] => list(value).map(value => {
    const file = object(value);
    const coordinate = object(file.parsed_episode);
    return { file_id: str(file.file_id), file_name: str(file.file_name), torrent_path: str(file.torrent_path) || str(file.file_name),
      show_name: str(file.show_name), parsed: {
        season_number: num(coordinate.season_number),
        episode_number: num(coordinate.episode_number) } };
  });
  return {
    preview_id: str(raw.preview_id), revision: num(raw.revision) ?? 0, expires_at: str(raw.expires_at),
    search_results: object(raw.search_results) as TorrentPreviewResponse['search_results'],
    episode_match_source: raw.episode_match_source === 'tmdb' ? 'tmdb' : raw.episode_match_source === 'tvdb' ? 'tvdb' : undefined,
    subtitles: list(raw.subtitles).map(str), subtitle_files: parsedFiles(raw.subtitle_files),
    skipped_files: list(raw.skipped_files).map(value => { const file = object(value); return {
      file_name: str(file.file_name), torrent_path: str(file.torrent_path), reason: str(file.reason) }; }),
    resource_id: num(raw.resource_id) ?? undefined,
    series: raw.series as TorrentPreviewResponse['series'],
    resource_candidates: raw.resource_candidates as TorrentPreviewResponse['resource_candidates'],
    torrent_name: str(raw.torrent_name),
    episode_catalog: normalizeEpisodeCatalog(raw.episode_catalog),
    parsed_files: parsedFiles(raw.parsed_files), specials: parsedFiles(raw.specials),
  };
}

/** Copy only coordinates; catalog titles and metadata never enter mappings. */
export function createEpisodeMapping(parsed: ParsedEpisodeRef, subjectId: number | null,
  bangumi: BangumiCatalogEpisode | null | undefined, tmdb: TmdbEpisodeRef | null | undefined,
  tvdb: TmdbEpisodeRef | null | undefined, source: EpisodeMatchSource | null): EpisodeMapping {
  const reference = (episode: TmdbEpisodeRef | null | undefined): TmdbEpisodeRef => episode
    ? { series_id: episode.series_id, episode_id: episode.episode_id,
        season_number: episode.season_number, episode_number: episode.episode_number } : emptyRef();
  return { parsed: { ...parsed },
    bangumi: { subject_id: subjectId, episode_id: bangumi?.episode_id ?? null,
      episode_number: bangumi?.episode_number ?? null, episode_absolute: bangumi?.episode_absolute ?? null },
    tmdb: reference(tmdb), tvdb: reference(tvdb), match_source: source };
}

export function catalogSeriesTitle(catalog: EpisodeCatalog, id: string): string | undefined {
  return catalog.tmdb_series_titles?.[id];
}

/** Preserve the pre-existing sort→ep fallback only inside legacy matching. */
export function matchingBangumiAbsolute(episode: BangumiCatalogEpisode): number | null {
  return episode.matching_absolute ?? episode.episode_absolute ?? episode.episode_number;
}
