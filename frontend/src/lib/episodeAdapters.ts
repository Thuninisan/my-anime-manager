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
      show_name: str(file.show_name),
      type: (['video', 'subtitle', 'font', 'audio', 'other'].includes(str(file.type)) ? file.type : 'other') as ParsedFile['type'],
      category: file.category === 'regular' || file.category === 'special' ? file.category : null,
      processing_status: (['automatic', 'manual', 'associate', 'ignored'].includes(str(file.processing_status)) ? file.processing_status : 'ignored') as ParsedFile['processing_status'],
      skip_reason: typeof file.skip_reason === 'string' ? file.skip_reason : null,
      parsed: {
        season_number: num(coordinate.season_number),
        episode_number: num(coordinate.episode_number) } };
  });
  return {
    preview_id: str(raw.preview_id), revision: num(raw.revision) ?? 0, expires_at: str(raw.expires_at),
    search_results: object(raw.search_results) as TorrentPreviewResponse['search_results'],
    episode_match_source: raw.episode_match_source === 'tmdb' ? 'tmdb' : raw.episode_match_source === 'tvdb' ? 'tvdb' : undefined,
    resource_id: num(raw.resource_id) ?? undefined,
    torrent_name: str(raw.torrent_name),
    episode_catalog: normalizeEpisodeCatalog(raw.episode_catalog),
    parsed_files: parsedFiles(raw.parsed_files),
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

/** Provisional recommendation only; final provider references live in each mapping. */
export function candidateIds(entry: import('@/types/matchTable').SearchEntry | undefined, provider: 'tmdb' | 'bangumi' | 'tvdb'): number[] {
  return (entry?.candidates[provider] ?? []).map(candidate => candidate.provider_id);
}
export function recommendedId(entry: import('@/types/matchTable').SearchEntry | undefined, provider: 'tmdb' | 'bangumi' | 'tvdb'): number | null {
  return candidateIds(entry, provider)[0] ?? null;
}

/** Central selectors keep file classification out of matching components. */
export const automaticVideoFiles = (files: ParsedFile[]): ParsedFile[] =>
  files.filter(file => file.type === 'video' && file.processing_status === 'automatic');
export const manualVideoFiles = (files: ParsedFile[]): ParsedFile[] =>
  files.filter(file => file.type === 'video' && file.processing_status === 'manual');
export const subtitleFiles = (files: ParsedFile[]): ParsedFile[] =>
  files.filter(file => file.type === 'subtitle' && file.processing_status === 'associate');
export const ignoredFiles = (files: ParsedFile[]): ParsedFile[] =>
  files.filter(file => file.processing_status === 'ignored');
export const processableVideoFiles = (files: ParsedFile[]): ParsedFile[] =>
  files.filter(file => file.type === 'video' && file.processing_status !== 'ignored');
