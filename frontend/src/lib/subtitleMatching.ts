import type { MatchRow } from '@/types/matchTable';

export interface SubtitleFile {
  id: string;
  name: string;
  path: string;
  source: 'torrent' | 'upload';
}
export interface SubtitleState { linked: SubtitleFile[]; candidates: SubtitleFile[] }
export type SubtitleAssociations = Record<string, SubtitleState>;
export type SubtitleFilter = 'all' | 'missing' | 'pending';
const base = (path: string) => path.split('/').pop() || path;
const stem = (path: string) => base(path).replace(/\.[^.]+$/, '').toLowerCase();
const clean = (path: string) => stem(path).replace(/(?:[._ -](?:chs|cht|sc|tc|zh|zh-cn|zh-tw|en|eng|ja|jpn|简中|繁中|简体|繁体|forced|default))+$/i, '');
const dir = (path: string) => path.slice(0, path.lastIndexOf('/') + 1);

/** Same-directory stem matches are safe; ambiguous/cross-directory matches need confirmation. */
export function associateSubtitles(rows: MatchRow[], files: SubtitleFile[], overrides: Record<string, string | null>): SubtitleAssociations {
  const result: SubtitleAssociations = Object.fromEntries(rows.map(r => [r.torrent_path, { linked: [], candidates: [] }]));
  for (const sub of files) {
    if (Object.prototype.hasOwnProperty.call(overrides, sub.id)) {
      const target = overrides[sub.id];
      if (target && result[target]) result[target].linked.push(sub);
      continue;
    }
    let names = rows.filter(r => stem(r.file_name) === clean(sub.path));
    // Episode-only numbers are never sufficient: require explicit season and show title.
    if (!names.length) {
      const episode = base(sub.path).match(/\bS(\d+)E(\d+)\b/i);
      const normalise = (value: string) => value.toLowerCase().replace(/[^\p{L}\p{N}]/gu, '');
      if (episode) names = rows.filter(r => r.src_season === Number(episode[1]) && r.src_episode === Number(episode[2])
        && normalise(r.show_name).length > 2 && normalise(base(sub.path)).includes(normalise(r.show_name)));
    }
    const exact = sub.source === 'torrent' ? names.filter(r => dir(r.torrent_path) === dir(sub.path)) : names;
    if (exact.length === 1) result[exact[0].torrent_path].linked.push(sub);
    else for (const row of names) result[row.torrent_path].candidates.push(sub);
  }
  return result;
}
export function subtitleStatus(state?: SubtitleState): SubtitleFilter | 'linked' {
  if (state?.candidates.length) return 'pending';
  return state?.linked.length ? 'linked' : 'missing';
}
