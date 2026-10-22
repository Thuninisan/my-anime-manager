import type { BangumiCatalogEpisode, CatalogEpisode, EpisodeCatalog, EpisodeMapping } from '@/types/episode';

export interface SequenceFillPlan {
  patches: Partial<EpisodeMapping>[];
  counts: Record<'bangumi' | 'tmdb' | 'tvdb', { filled: number; cleared: number }>;
  warnings: string[];
  active: boolean;
}

// 各平台只读取起点所属的最小剧集集合，缺号时沿实际目录前进。
export function buildSequenceFillPlan(anchor: EpisodeMapping, count: number, catalog: EpisodeCatalog): SequenceFillPlan {
  const plan: SequenceFillPlan = {
    patches: Array.from({ length: count }, () => ({})), active: false, warnings: [],
    counts: { bangumi: { filled: 0, cleared: 0 }, tmdb: { filled: 0, cleared: 0 }, tvdb: { filled: 0, cleared: 0 } },
  };
  function sequence<T extends BangumiCatalogEpisode | CatalogEpisode>(
    provider: 'bangumi' | 'tmdb' | 'tvdb', episodes: T[], number: (ep: T) => number | null,
    matches: (ep: T) => boolean, apply: (ep: T | null, patch: Partial<EpisodeMapping>) => void,
  ) {
    const sorted = [...episodes].sort((a, b) => (number(a) ?? Infinity) - (number(b) ?? Infinity));
    const numbers = sorted.map(number);
    if (numbers.some(n => n == null || !Number.isFinite(n)) || new Set(numbers).size !== numbers.length) {
      plan.warnings.push(`${provider.toUpperCase()} 目录集号缺失或重复，已跳过`);
      return;
    }
    const start = sorted.findIndex(matches);
    if (start < 0) {
      plan.warnings.push(`${provider.toUpperCase()} 起点不在当前目录中，已跳过`);
      return;
    }
    plan.active = true;
    plan.patches.forEach((patch, i) => {
      const ep = sorted[start + i + 1] ?? null;
      apply(ep, patch);
      plan.counts[provider][ep ? 'filled' : 'cleared']++;
    });
  }
  const bgm = anchor.bangumi;
  if (bgm.subject_id != null && bgm.episode_id != null) {
    sequence('bangumi', catalog.bangumi[String(bgm.subject_id)]?.episodes ?? [], ep => ep.episode_absolute,
      ep => ep.episode_id === bgm.episode_id,
      (ep, patch) => { patch.bangumi = ep ? {
        subject_id: bgm.subject_id, episode_id: ep.episode_id,
        episode_number: ep.episode_number, episode_absolute: ep.episode_absolute,
      } : { subject_id: bgm.subject_id, episode_id: null, episode_number: null, episode_absolute: null }; });
  }
  for (const provider of ['tmdb', 'tvdb'] as const) {
    const ref = anchor[provider];
    if (ref.series_id == null || ref.season_number == null || ref.episode_number == null) continue;
    const seasons = provider === 'tmdb' ? catalog.tmdb[String(ref.series_id)] : catalog.tvdb[String(ref.series_id)]?.seasons;
    sequence(provider, seasons?.[String(ref.season_number)]?.episodes ?? [], ep => ep.episode_number,
      ep => ep.episode_number === ref.episode_number && (ref.episode_id == null || ep.episode_id === ref.episode_id),
      (ep, patch) => { patch[provider] = {
        series_id: ref.series_id, season_number: ref.season_number,
        episode_id: ep?.episode_id ?? null, episode_number: ep?.episode_number ?? null,
      }; });
  }
  return plan;
}

// 页面可保留季选择；提交时未选具体剧集的平台必须使用完整空引用。
export function submissionEpisodeMapping(mapping: EpisodeMapping): EpisodeMapping {
  const result = { ...mapping };
  for (const provider of ['tmdb', 'tvdb'] as const) {
    if (mapping[provider].episode_number == null) {
      result[provider] = { series_id: null, season_number: null, episode_id: null, episode_number: null };
    }
  }
  return result;
}
