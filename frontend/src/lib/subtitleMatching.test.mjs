import { readFileSync } from 'node:fs';
import ts from 'typescript';
import assert from 'node:assert/strict';
const source = readFileSync(new URL('./subtitleMatching.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { associateSubtitles, subtitleStatus, subtitleLanguage, preferenceSelects, selectSubtitles, subtitlePeers, subtitleDestinationSuffix } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const rows = ['A', 'B'].map(dir => ({ torrent_path: `${dir}/Show - 01.mkv`, file_name: 'Show - 01.mkv', show_name: 'Show', src_season: 1, src_episode: 1 }));
const file = path => ({ id: `torrent:${path}`, path, name: path.split('/').pop(), source: 'torrent' });
const same = file('A/Show - 01.chs.ass');
let result = associateSubtitles(rows, [same], {});
assert.equal(result[rows[0].torrent_path].linked[0], same);
assert.equal(subtitleStatus(result[rows[1].torrent_path]), 'missing');
const other = file('Subs/Show - 01.cht.ass');
result = associateSubtitles(rows, [other], {});
assert.equal(subtitleStatus(result[rows[0].torrent_path]), 'pending');
assert.equal(result[rows[1].torrent_path].linked.length, 0);
result = associateSubtitles(rows, [other], { [other.id]: rows[1].torrent_path });
assert.equal(result[rows[1].torrent_path].linked.length, 1);
assert.equal(result[rows[0].torrent_path].candidates.length, 0);
assert.equal(associateSubtitles(rows, [same], { [same.id]: null })[rows[0].torrent_path].linked.length, 0);
assert.equal(associateSubtitles(rows, [file('A/01.ass')], {})[rows[0].torrent_path].linked.length, 0);
const uploaded = { id: 'upload:renamed_2.ass', name: 'original.ass', path: 'renamed_2.ass', source: 'upload' };
assert.equal(associateSubtitles(rows, [same, uploaded], { [uploaded.id]: rows[0].torrent_path })[rows[0].torrent_path].linked.length, 2);
console.log('Subtitle associations: 7 scenarios passed');

for (const [name, expected] of [
  ['Show.chs.ass', 'simplified'], ['Show.SC.srt', 'simplified'], ['Show.zh-CN.ass', 'simplified'],
  ['Show.zh_Hans.ass', 'simplified'], ['Show.简中.ass', 'simplified'],
  ['Show.cht.ass', 'traditional'], ['Show.TC.srt', 'traditional'], ['Show.zh-TW.ass', 'traditional'],
  ['Show.zh-Hant.ass', 'traditional'], ['Show.繁体.ass', 'traditional'],
  ['Show.简繁双语.ass', 'bilingual'], ['Show.chs+cht.ass', 'bilingual'],
  ['Show.chs.cht.ass', 'unknown'], ['Show.zh.ass', 'unknown'], ['Scandal.ass', 'unknown'],
  ['Show.eng.ass', 'other'], ['Show.jpn.ass', 'other'],
]) assert.equal(subtitleLanguage(name), expected, name);
const simplified = file('A/Show.chs.ass');
const traditional = file('A/Show.cht.ass');
const unknown = file('A/Show.ass');
const english = file('A/Show.eng.ass');
const linked = { video: { linked: [simplified, traditional, unknown, english], candidates: [] } };
let chosen = selectSubtitles(linked, 'simplified', {});
assert.deepEqual(chosen.video.selected, [simplified, unknown]);
assert.equal(chosen.video.linked.length, 4);
assert.deepEqual(selectSubtitles(linked, 'traditional', {}).video.selected, [traditional, unknown]);
assert.deepEqual(selectSubtitles(linked, 'all', {}).video.selected, linked.video.linked);
assert.deepEqual(selectSubtitles(linked, 'simplified', { [traditional.id]: true, [unknown.id]: false }).video.selected, [simplified, traditional]);
assert.equal(subtitleStatus(selectSubtitles({ video: { linked: [traditional], candidates: [] } }, 'simplified', {}).video), 'missing');
assert.equal(preferenceSelects({ ...uploaded, name: 'Show.cht.ass' }, 'simplified'), false);
const idx = file('A/Show.chs.idx');
const sub = file('A/Show.chs.sub');
assert.deepEqual(subtitlePeers(idx, [idx, sub, traditional]), [idx, sub]);
assert.deepEqual(subtitlePeers(file('B/Show.chs.idx'), [idx, sub]), []);
assert.equal(subtitleDestinationSuffix(simplified, [simplified, traditional]), '.zh-CN.ass');
assert.equal(subtitleDestinationSuffix(traditional, [simplified, traditional]), '.zh-TW.ass');
assert.equal(subtitleDestinationSuffix(idx, [idx, sub]), '.zh-CN.idx');
assert.equal(subtitleDestinationSuffix(sub, [idx, sub]), '.zh-CN.sub');
const second = file('A/Show.sc.ass');
assert.equal(subtitleDestinationSuffix(second, [simplified, second]), '.sub2.zh-CN.ass');
assert.notEqual(subtitleDestinationSuffix(unknown, [unknown, english]), subtitleDestinationSuffix(english, [unknown, english]));
const duplicateUpload = { ...uploaded, id: 'upload:another.ass', path: 'another.ass' };
assert.notEqual(subtitleDestinationSuffix(uploaded, [uploaded, duplicateUpload]), subtitleDestinationSuffix(duplicateUpload, [uploaded, duplicateUpload]));
console.log('Subtitle language, preference, manual overrides, pairing and destination tests passed');
