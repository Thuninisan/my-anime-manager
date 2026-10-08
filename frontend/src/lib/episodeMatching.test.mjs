import { readFileSync } from 'node:fs';
import ts from 'typescript';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToString } from 'react-dom/server';

function moduleUrl(path, imports = {}) {
  let source = ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
  }).outputText;
  for (const [name, url] of Object.entries(imports)) source = source.replaceAll(`'${name}'`, `'${url}'`);
  return `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
}
const adaptersUrl = moduleUrl('./episodeAdapters.ts');
const matchingUrl = moduleUrl('./matchUtils.ts', { './episodeAdapters': adaptersUrl });
const hookUrl = moduleUrl('../hooks/useMatchOverrides.ts', {
  '@/lib/episodeAdapters': adaptersUrl, '@/lib/matchUtils': matchingUrl,
  react: import.meta.resolve('react'),
});
const { normalizeTorrentPreview, normalizeEpisodeCatalog,
  createEpisodeMapping } = await import(adaptersUrl);
const { computeMatches, buildTmdbEpOptions, buildSpSeasonOptions, DuplicateEpisodeError, matchEpisodeTitles, buildTvdbSeasonOptions } = await import(matchingUrl);
const { useMatchOverrides } = await import(hookUrl);
const empty = { series_id: null, episode_id: null, season_number: null, episode_number: null };

function fixture({ parsedSeason = 1, parsedEpisode = 3, tmdbSeason = 1, tmdbEpisode = 3,
  tvdbSeason = 1, tvdbEpisode = 3, bgmEp = 3, bgmSort = 3, index = 'tvdb', tvdb = true } = {}) {
  return normalizeTorrentPreview({ episode_match_source: index, torrent_name: 'Show', torrent_path: '/tmp/show.torrent',
    parsed_files: [{ file_name: 'Show.mkv', torrent_path: 'Show.mkv', show_name: 'Show', parsed_episode: { season_number: parsedSeason, episode_number: parsedEpisode } }],
    search_results: { Show: { tmdb_series_id: 100, display_name: 'Show', bangumi_subject_id: 200, bangumi_display_name: 'Show',
      mapping_hints: [{ bangumi_subject_id: 200, name: 'Show', tvdb_series_id: 300 }] } },
    episode_catalog: {
      tmdb: { 100: { [tmdbSeason]: { name: 'Season', episodes: [{ episode_number: tmdbEpisode, episode_id: 101, name: 'First Day',
        overview: 'Plot', stillPath: '/still.jpg', airDate: '2026-01-01', voteAverage: 8,
        guestStars: [{ name: 'Actor', character: 'Hero' }] }] } } },
      bangumi: { 200: { name: 'Show', episodes: [{ episode_id: 201, episode_number: bgmEp, matching_absolute: bgmSort, episode_absolute: bgmSort, name: 'First Day' }] } },
      tvdb: tvdb ? { 300: { name: 'Show', seasons: { [tvdbSeason]: { name: 'Season', episodes: [
        { episode_number: tvdbEpisode, episode_id: 301, name: 'First Day', episode_absolute: bgmSort }] } } } } : {},
    },
  });
}
// 1. Ordinary coordinates; identity belongs to selected episodes.
let data = fixture();
let row = computeMatches(data)[0];
assert.deepEqual(row.mapping, {
  parsed: { season_number: 1, episode_number: 3 },
  bangumi: { subject_id: 200, episode_id: 201, episode_number: 3, episode_absolute: 3 },
  tmdb: { series_id: 100, episode_id: 101, season_number: 1, episode_number: 3 },
  tvdb: { series_id: 300, episode_id: 301, season_number: 1, episode_number: 3 }, match_source: 'tvdb',
});
assert.equal('src_episode' in row, false);
assert.equal('name' in row.mapping.tmdb, false);
// 2. Independent numbering; legacy Bangumi-first and TVDB-first retain behavior.
for (const index of [undefined, 'tvdb']) {
  data = fixture({ parsedEpisode: 15, tmdbSeason: 2, tmdbEpisode: 3, tvdbEpisode: 15, bgmSort: 15, index });
  if (index === undefined) delete data.episode_match_source;
  row = computeMatches(data)[0];
  assert.equal(row.mapping.parsed.episode_number, 15);
  assert.equal(row.mapping.tmdb.season_number, 2);
  assert.equal(row.mapping.tmdb.episode_number, 3);
  assert.equal(row.mapping.tvdb.season_number, 1);
  assert.equal(row.mapping.tvdb.episode_number, 15);
  assert.equal(row.mapping.bangumi.episode_absolute, 15);
}
// 3. Specials, including dropdowns and download compatibility.
for (const index of ['tmdb', 'tvdb', undefined]) {
  data = fixture({ parsedSeason: 0, tmdbSeason: 0, tvdbSeason: 0, index });
  if (index === undefined) delete data.episode_match_source;
  row = computeMatches(data)[0];
  assert.equal(row.mapping.parsed.season_number, 0);
  assert.equal(row.mapping.tmdb.season_number, 0);
  assert.equal(row.mapping.tvdb.season_number, 0);
  assert.equal(buildTmdbEpOptions(0, data.episode_catalog.tmdb[100])[0].episode_number, 3);
  assert.equal(row.mapping.tmdb.season_number, 0);
}
// 4. An unmatched provider has nulls, never fake zero coordinates.
data = fixture({ index: 'tmdb', tvdb: false });
row = computeMatches(data)[0];
assert.equal(row.matched, true);
assert.deepEqual(row.mapping.tvdb, empty);
// 5. Bangumi ep != sort, including decimal ep and zero sort.
for (const bgmSort of [15, 0]) {
  data = fixture({ index: 'tmdb', bgmEp: 2.5, bgmSort });
  row = computeMatches(data)[0];
  assert.equal(row.mapping.bangumi.episode_number, 2.5);
  assert.equal(row.mapping.bangumi.episode_absolute, bgmSort);
}
// Missing sort stays null; compatibility matching still uses the established fallback.
data = fixture({ bgmSort: null });
delete data.episode_match_source;
row = computeMatches(data)[0];
assert.equal(row.mapping.bangumi.episode_absolute, null);
assert.equal(row.mapping.bangumi.episode_number, 3);
// Projection never retains NFO metadata on the frontend.
data = fixture();
const catalogWire = JSON.stringify(data.episode_catalog);
for (const field of ['overview', 'stillPath', 'guestStars', 'Plot']) assert.equal(catalogWire.includes(field), false);
assert.equal('epNum' in data.episode_catalog.tmdb[100][1].episodes[0], false);
// Identical coordinates across series keep the exact candidate selected by fuzzy matching.
data = fixture({ index: 'tmdb' });
const other = normalizeEpisodeCatalog({ tvdb: { 999: { name: 'Other', seasons: { 1: { name: 'Season', episodes: [
  { episode_number: 3, episode_id: 998, name: 'Unrelated' }] } } } } });
data.episode_catalog.tvdb = { ...other.tvdb, ...data.episode_catalog.tvdb };
row = computeMatches(data)[0];
assert.equal(row.mapping.tvdb.series_id, 300);
assert.equal(row.mapping.tvdb.episode_id, 301);
// Unassociated TMDB series cannot participate even with an identical episode title.
data = fixture({ index: 'tvdb' });
data.episode_catalog.tmdb[100][1].episodes[0].name = 'Unrelated';
data.episode_catalog.tmdb[999] = normalizeEpisodeCatalog({ tmdb: { 999: { 2: { name: 'Sequel', episodes: [
  { episode_number: 8, episode_id: 998, name: 'First Day' }] } } } }).tmdb[999];
row = computeMatches(data)[0];
assert.equal(row.mapping.tmdb.series_id, null);
assert.equal(row.mapping.tmdb.episode_id, null);
assert.equal(row.matched, false);
// Exercise the actual React hook: overrides preserve parsed coordinates and unrelated providers.
function overriddenRows(data, actions) {
  let effective;
  function Harness() {
    const [applied, setApplied] = React.useState(false);
    const state = useMatchOverrides(data, data.search_results, data.episode_catalog);
    if (!applied) { actions(state); setApplied(true); }
    effective = state.rows;
    return null;
  }
  renderToString(React.createElement(Harness));
  return effective;
}
data = fixture({ index: 'tmdb', tvdb: false });
data.episode_catalog.tmdb[100][0] = normalizeEpisodeCatalog({ tmdb: { 100: { 0: { name: 'Specials', episodes: [
  { episode_number: 7, episode_id: 777, name: 'Special' }] } } } }).tmdb[100][0];
row = overriddenRows(data, state => state.handleTmdbSeasonChange(0, 'Show', '100:0'))[0];
assert.deepEqual(row.mapping.parsed, { season_number: 1, episode_number: 3 });
assert.deepEqual(row.mapping.tmdb, { series_id: 100, episode_id: 777, season_number: 0, episode_number: 7 });
assert.deepEqual(row.mapping.tvdb, empty);
assert.equal(row.mapping.bangumi.episode_id, 201);
// Manual provider/episode changes pick new identity and keep Bangumi ep distinct.
data = fixture();
data.episode_catalog.bangumi[200].episodes.push({ subject_id: 200, episode_id: 202, episode_number: 9, episode_absolute: 16, name: 'Next' });
row = overriddenRows(data, state => state.handleBgmEpChange(0, 200, '202'))[0];
assert.equal(row.mapping.bangumi.episode_number, 9);
assert.equal(row.mapping.bangumi.episode_absolute, 16);
assert.equal(row.mapping.tmdb.episode_number, 3);
assert.equal(buildSpSeasonOptions(data.episode_catalog, data.search_results, 'tvdb')[0].value, '300:1');
const copied = createEpisodeMapping({ season_number: 0, episode_number: 1 }, null, null, null, null, null);
assert.deepEqual(copied.tvdb, empty);
data.parsed_files.push({ ...data.parsed_files[0], file_name: 'Duplicate.mkv' });
assert.throws(() => computeMatches(data), DuplicateEpisodeError);

// Explicit canonical nulls take precedence over legacy aliases at the boundary.
const explicitNull = normalizeTorrentPreview({ parsed_files: [{ file_name: 'Unknown.mkv', season: 1, episode: 3,
  parsed_episode: { season_number: null, episode_number: null } }] });
assert.deepEqual(explicitNull.parsed_files[0].parsed, { season_number: null, episode_number: null });
const unknownId = normalizeEpisodeCatalog({ tmdb: { 100: { 1: { episodes: [{ episode_number: 3, episode_id: null }] } } } });
assert.equal(unknownId.tmdb[100][1].episodes[0].episode_id, null);
// Download carries the exact manual mapping and provider IDs alongside the legacy bridge.
const submitted = { mapping: row.mapping };
assert.deepEqual(submitted.mapping, row.mapping);
assert.equal(submitted.mapping.tmdb.series_id, 100);
assert.equal(submitted.mapping.bangumi.episode_number, 9);
assert.equal(submitted.mapping.bangumi.episode_absolute, 16);
assert.ok(!('title' in submitted.mapping));
console.log('Episode matching: canonical coordinates, identity, overrides, compatibility and duplicate scenarios passed');

// Multi-series catalogs with identical episode names cannot leak identities.
for (const index of ['tmdb', 'tvdb', undefined]) {
  data = fixture({ index });
  data.search_results.Other = { tmdb_series_id: 400, display_name: 'Other', bangumi_subject_id: 500, bangumi_display_name: 'Other',
    mapping_hints: [{ bangumi_subject_id: 500, name: 'Other', tvdb_series_id: 600 }] };
  data.parsed_files.push({ ...data.parsed_files[0], file_name: 'Other.mkv', torrent_path: 'Other.mkv', show_name: 'Other' });
  const otherCatalog = normalizeEpisodeCatalog({
    tmdb: { 400: { 1: { name: 'Season', episodes: [{ episode_number: 3, episode_id: 401, name: 'First Day' }] } } },
    bangumi: { 500: { name: 'Other', episodes: [{ episode_id: 501, episode_number: 3, matching_absolute: 3, episode_absolute: 3, name: 'First Day' }] } },
    tvdb: { 600: { name: 'Other', seasons: { 1: { episodes: [{ episode_number: 3, episode_id: 601, name: 'First Day', episode_absolute: 3 }] } } } },
  });
  for (const provider of ['tmdb', 'bangumi', 'tvdb']) Object.assign(data.episode_catalog[provider], otherCatalog[provider]);
  const matches = computeMatches(data);
  assert.equal(matches[0].mapping.tmdb.series_id, 100);
  assert.equal(matches[1].mapping.tmdb.series_id, 400);
  assert.equal(matches[0].mapping.bangumi.subject_id, 200);
  assert.equal(matches[1].mapping.bangumi.subject_id, 500);
  assert.equal(matches[0].mapping.tvdb.series_id, 300);
  assert.equal(matches[1].mapping.tvdb.series_id, 600);
}
console.log('Multi-series resource isolation: all matching modes passed');

// Linked multi-season directories participate without a primary Bangumi identity.
data = fixture({ index: 'tmdb', parsedEpisode: 25, tmdbEpisode: 25, tvdbSeason: 2, tvdbEpisode: 1 });
data.search_results.Show.bangumi_subject_id = null;
data.search_results.Show.bangumi_subject_ids = [200, 250];
data.episode_catalog.bangumi[200].episodes[0].name = 'Opening';
data.episode_catalog.bangumi[250] = { name: 'Season 2', episodes: [
  { subject_id: 250, episode_id: 251, episode_number: 1, episode_absolute: 1, matching_absolute: 1, name: 'First Day', name_cn: '' }] };
row = computeMatches(data)[0];
assert.equal(row.matched, true);
assert.equal(row.mapping.bangumi.subject_id, 250);
assert.equal(row.mapping.bangumi.episode_number, 1);
assert.equal(row.mapping.tmdb.episode_number, 25);
assert.equal(row.mapping.tvdb.episode_number, 1);
assert.equal(row.mapping.tvdb.season_number, 2);
// Explicit index switches use parsed coordinates, never silently switch back.
data.episode_match_source = 'tvdb';
assert.equal(computeMatches(data)[0].matched, false);
assert.equal(computeMatches(data)[0].match_status.tvdb, 'missing');
data.parsed_files[0].parsed = { season_number: 2, episode_number: 1 };
row = computeMatches(data)[0];
assert.equal(row.matched, true);
assert.equal(row.mapping.tmdb.episode_number, 25);
assert.equal(row.mapping.bangumi.subject_id, 250);
// Duplicate titles remain candidates independent of input order.
const titles = [{ name: 'First Day', id: 1 }, { name: 'First Day', id: 2 }];
for (const ordered of [titles, titles.toReversed()]) {
  const result = matchEpisodeTitles('First Day', ordered);
  assert.equal(result.status, 'ambiguous');
  assert.equal(result.selected, null);
  assert.equal(result.candidates.length, 2);
}
assert.equal(matchEpisodeTitles('abcd', [{ name: 'dcba', id: 1 }, { name: 'abcd', id: 2 }]).selected.id, 2);
assert.equal(matchEpisodeTitles('abcdefghijk', [{ name: 'abcdefghijx' }, { name: 'abcdefghijy' }]).status, 'ambiguous');
data.episode_catalog.bangumi[200].episodes[0].name = 'First Day';
row = computeMatches(data)[0];
assert.equal(row.match_status.bangumi, 'ambiguous');
assert.equal(row.mapping.bangumi.subject_id, null);
assert.equal(row.matched, false);
// Render-phase rerenders exercise preservation across an index change and reordering.
let switched;
function SwitchHarness() {
  const [phase, setPhase] = React.useState(0);
  const original = fixture({ index: 'tmdb' });
  original.parsed_files[0].file_id = 'stable-file';
  const secondFile = { ...original.parsed_files[0], file_id: 'second-file', file_name: 'Second.mkv', torrent_path: 'Second.mkv', parsed: { season_number: 1, episode_number: 4 } };
  original.parsed_files.push(secondFile);
  const next = fixture({ index: 'tvdb', tvdbEpisode: 3, tmdbEpisode: 8 });
  next.parsed_files[0].file_id = 'stable-file';
  next.parsed_files.unshift(secondFile);
  const current = phase === 0 ? original : next;
  const state = useMatchOverrides(current, current.search_results, current.episode_catalog);
  if (phase === 0) { state.handleBgmEpChange(0, 200, '201'); setPhase(1); }
  switched = state.rows.find(row => row.torrent_path === 'Show.mkv');
  return null;
}
renderToString(React.createElement(SwitchHarness));
assert.equal(switched.mapping.tmdb.episode_number, 8);
assert.equal(switched.mapping.bangumi.episode_id, 201);
assert.equal(switched.mapping.match_source, 'tvdb');
console.log('Selectable episode indexes, multi-Subject catalogs, title ambiguity and manual override preservation passed');

// TVDB manual selectors carry series IDs and exclude other shows' directories.
data = fixture();
data.episode_catalog.tvdb[999] = data.episode_catalog.tvdb[300];
const scopedOptions = buildTvdbSeasonOptions(0, 'Show', data.search_results, data.episode_catalog);
assert.equal(scopedOptions.tvdbShowId, 300);
assert.deepEqual(scopedOptions.opts.map(option => option.value), ['300:1']);
