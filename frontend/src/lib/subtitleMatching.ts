import type { MatchRow } from '@/types/matchTable';

export interface SubtitleFile {
  id: string;
  name: string;
  path: string;
  source: 'torrent' | 'upload';
}
export interface SubtitleState { linked: SubtitleFile[]; candidates: SubtitleFile[]; selected?: SubtitleFile[] }
export type SubtitleAssociations = Record<string, SubtitleState>;
export type SubtitleFilter = 'all' | 'missing' | 'pending';
const base = (path: string) => path.split('/').pop() || path;
const stem = (path: string) => base(path).replace(/\.[^.]+$/, '').toLowerCase();
const clean = (path: string) => stem(path).replace(/(?:[._ -](?:chs|cht|sc|tc|zh|zh-cn|zh-tw|zh-hans|zh-hant|en|eng|ja|jpn|简中|繁中|简体|繁体|forced|default))+$/i, '');
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
      if (episode) names = rows.filter(r => r.mapping.parsed.season_number === Number(episode[1]) && r.mapping.parsed.episode_number === Number(episode[2])
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
  return (state?.selected ?? state?.linked)?.length ? 'linked' : 'missing';
}

export type SubtitlePreference = 'all' | 'simplified' | 'traditional';
export type SubtitleLanguage = 'simplified' | 'traditional' | 'bilingual' | 'other' | 'unknown';
export const languageLabels: Record<SubtitleLanguage, string> = {
  simplified: '简体', traditional: '繁体', bilingual: '简繁双语', other: '其他语言', unknown: '语言未知',
};
export function subtitleLanguage(name: string): SubtitleLanguage {
  const tagged = (pattern: string) => new RegExp(`(?:^|[\\s._\\[\\](){}-])(?:${pattern})(?=$|[\\s._\\[\\](){}-])`, 'i').test(stem(name));
  const simplified = tagged('chs|sc|zh[-_]cn|zh[-_]hans|简体|简中');
  const traditional = tagged('cht|tc|zh[-_]tw|zh[-_]hant|繁体|繁中');
  if (tagged('简繁|简繁双语|简繁体|chs[+&/]cht|sc[+&/]tc')) return 'bilingual';
  if (simplified && traditional) return 'unknown';
  if (simplified) return 'simplified';
  if (traditional) return 'traditional';
  if (tagged('en|eng|english|ja|jpn|japanese|英文|日文')) return 'other';
  return 'unknown';
}
export function preferenceSelects(file: SubtitleFile, preference: SubtitlePreference): boolean {
  const language = subtitleLanguage(file.name);
  return preference === 'all' || language === 'unknown' || language === 'bilingual' || language === preference;
}
/** VobSub .idx/.sub files are a single selection and naming group. */
export function subtitleGroup(file: SubtitleFile): string {
  if (!/\.(idx|sub)$/i.test(file.path)) return file.id;
  const name = file.source === 'torrent' ? file.path : file.name;
  return `${file.source}:${name.replace(/\.[^.]+$/, '')}`;
}
export function subtitlePeers(file: SubtitleFile, files: SubtitleFile[]): SubtitleFile[] {
  if (!/\.(idx|sub)$/i.test(file.path)) return [file];
  return files.filter(other => /\.(idx|sub)$/i.test(other.path) && subtitleGroup(other) === subtitleGroup(file));
}
export function selectSubtitles(associations: SubtitleAssociations, preference: SubtitlePreference, overrides: Record<string, boolean>): SubtitleAssociations {
  return Object.fromEntries(Object.entries(associations).map(([path, state]) => [path, {
    ...state, selected: state.linked.filter(file => overrides[file.id] ?? preferenceSelects(file, preference)),
  }]));
}
export function subtitleDestinationSuffix(file: SubtitleFile, selected: SubtitleFile[]): string {
  const language = subtitleLanguage(file.name);
  const tag = language === 'simplified' ? '.zh-CN' : language === 'traditional' ? '.zh-TW' : '';
  const sameLanguage = selected.filter(other => tag ? subtitleLanguage(other.name) === language : !['simplified', 'traditional'].includes(subtitleLanguage(other.name)));
  const groups = [...new Set(sameLanguage.map(subtitleGroup))];
  const numbered = tag ? groups.length > 1 : selected.length > 1;
  const number = numbered ? `.sub${groups.indexOf(subtitleGroup(file)) + 1}` : '';
  return `${number}${tag}${file.path.slice(file.path.lastIndexOf('.'))}`;
}
