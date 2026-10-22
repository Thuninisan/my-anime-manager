import { apiFetch } from './client';
import { searchBangumiOnline, type BangumiOnlineResult } from './rssApi';

export type SearchProvider = 'bangumi' | 'tmdb' | 'tvdb';

export interface SearchResultItem {
  provider: SearchProvider;
  provider_id: number;
  media_type?: 'tv' | 'movie';
  name: string;
  name_original: string;
  date: string;
  poster_url: string;
}

export function bangumiSearchItem(item: BangumiOnlineResult): SearchResultItem {
  return {
    provider: 'bangumi', provider_id: item.bangumi_id, name: item.name,
    name_original: item.name_original, date: item.date, poster_url: item.poster_url,
  };
}

export async function searchCandidates(provider: SearchProvider, mediaType: 'tv' | 'movie', query: string): Promise<SearchResultItem[]> {
  if (provider === 'bangumi') return (await searchBangumiOnline(query)).map(bangumiSearchItem);
  const params = new URLSearchParams({ provider, media_type: mediaType, q: query });
  return apiFetch<SearchResultItem[]>(`/api/discovery/search?${params}`, { cache: 'no-store' }, 120_000);
}
