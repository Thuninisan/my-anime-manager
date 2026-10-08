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
const { computeMatches, buildTmdbEpOptions, buildSpSeasonOptions, DuplicateEpisodeError } = await import(matchingUrl);
const { useMatchOverrides } = await import(hookUrl);
const empty = { series_id: null, episode_id: null, season_number: null, episode_number: null };

function fixture({ parsedSeason = 1, parsedEpisode = 3, tmdbSeason = 1, tmdbEpisode = 3,
  tvdbSeason = 1, tvdbEpisode = 3, bgmEp = 3, bgmSort = 3, index = 'tvdb', tvdb = true } = {}) {
  return normalizeTorrentPreview({ index, torrent_name: 'Show', torrent_path: '/tmp/show.torrent',
    parsed_files: [{ file_name: 'Show.mkv', torrent_path: 'Show.mkv', show_name: 'Show', season: parsedSeason, episode: parsedEpisode }],
    search_results: { Show: { tmdb: { id: 100, name: 'Show' }, bangumi: { id: 200, name: 'Show' },
      map_entries: [{ bangumi_id: 200, name: 'Show', tvdb_id: 300 }] } },
    episode_data: {
      tmdb: { 100: { [tmdbSeason]: { name: 'Season', episodes: [{ epNum: tmdbEpisode, tmdbId: 101, name: 'First Day',
        overview: 'Plot', stillPath: '/still.jpg', airDate: '2026-01-01', voteAverage: 8,
        guestStars: [{ name: 'Actor', character: 'Hero' }] }] } } },
      bangumi: { 200: { name: 'Show', episodes: [{ id: 201, ep: bgmEp, sort: bgmSort, raw_sort: bgmSort, name: 'First Day' }] } },
      tvdb: tvdb ? { 300: { name: 'Show', seasons: { [tvdbSeason]: { name: 'Season', episodes: [
        { epNum: tvdbEpisode, tvdbId: 301, name: 'First Day', absoluteNumber: bgmSort }] } } } } : {},
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
  if (index === undefined) delete data.index;
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
  if (index === undefined) delete data.index;
  row = computeMatches(data)[0];
  assert.equal(row.mapping.parsed.season_number, 0);
  assert.equal(row.mapping.tmdb.season_number, 0);
  assert.equal(row.mapping.tvdb.season_number, 0);
  assert.equal(buildTmdbEpOptions(0, data.episode_data.tmdb[100])[0].episode_number, 3);
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
delete data.index;
row = computeMatches(data)[0];
assert.equal(row.mapping.bangumi.episode_absolute, null);
assert.equal(row.mapping.bangumi.episode_number, 3);
// Projection never retains NFO metadata on the frontend.
data = fixture();
const catalogWire = JSON.stringify(data.episode_data);
for (const field of ['overview', 'stillPath', 'guestStars', 'Plot']) assert.equal(catalogWire.includes(field), false);
assert.equal('epNum' in data.episode_data.tmdb[100][1].episodes[0], false);
// Identical coordinates across series keep the exact candidate selected by fuzzy matching.
data = fixture({ index: 'tmdb' });
const other = normalizeEpisodeCatalog({ tvdb: { 999: { name: 'Other', seasons: { 1: { name: 'Season', episodes: [
  { epNum: 3, tvdbId: 998, name: 'Unrelated' }] } } } } });
data.episode_data.tvdb = { ...other.tvdb, ...data.episode_data.tvdb };
row = computeMatches(data)[0];
assert.equal(row.mapping.tvdb.series_id, 300);
assert.equal(row.mapping.tvdb.episode_id, 301);
// TMDB merging keeps candidate series identity when matching a sequel.
data = fixture({ index: 'tvdb' });
data.episode_data.tmdb[100][1].episodes[0].name = 'Unrelated';
data.episode_data.tmdb[999] = normalizeEpisodeCatalog({ tmdb: { 999: { 2: { name: 'Sequel', episodes: [
  { epNum: 8, tmdbId: 998, name: 'First Day' }] } } } }).tmdb[999];
row = computeMatches(data)[0];
assert.equal(row.mapping.tmdb.series_id, 999);
assert.equal(row.mapping.tmdb.episode_id, 998);
assert.equal(row.mapping.tmdb.episode_number, 8);
// Exercise the actual React hook: overrides preserve parsed coordinates and unrelated providers.
function overriddenRows(data, actions) {
  let effective;
  function Harness() {
    const [applied, setApplied] = React.useState(false);
    const state = useMatchOverrides(data, data.search_results, data.episode_data);
    if (!applied) { actions(state); setApplied(true); }
    effective = state.rows;
    return null;
  }
  renderToString(React.createElement(Harness));
  return effective;
}
data = fixture({ index: 'tmdb', tvdb: false });
data.episode_data.tmdb[100][0] = normalizeEpisodeCatalog({ tmdb: { 100: { 0: { name: 'Specials', episodes: [
  { epNum: 7, tmdbId: 777, name: 'Special' }] } } } }).tmdb[100][0];
row = overriddenRows(data, state => state.handleTmdbSeasonChange(0, 'Show', '100:0'))[0];
assert.deepEqual(row.mapping.parsed, { season_number: 1, episode_number: 3 });
assert.deepEqual(row.mapping.tmdb, { series_id: 100, episode_id: 777, season_number: 0, episode_number: 7 });
assert.deepEqual(row.mapping.tvdb, empty);
assert.equal(row.mapping.bangumi.episode_id, 201);
// Manual provider/episode changes pick new identity and keep Bangumi ep distinct.
data = fixture();
data.episode_data.bangumi[200].episodes.push({ subject_id: 200, episode_id: 202, episode_number: 9, episode_absolute: 16, name: 'Next' });
row = overriddenRows(data, state => state.handleBgmEpChange(0, 200, '202'))[0];
assert.equal(row.mapping.bangumi.episode_number, 9);
assert.equal(row.mapping.bangumi.episode_absolute, 16);
assert.equal(row.mapping.tmdb.episode_number, 3);
assert.equal(buildSpSeasonOptions(data.episode_data, data.search_results, 'tvdb')[0].value, '300:1');
const copied = createEpisodeMapping({ season_number: 0, episode_number: 1 }, null, null, null, null, null);
assert.deepEqual(copied.tvdb, empty);
data.parsed_files.push({ ...data.parsed_files[0], file_name: 'Duplicate.mkv' });
assert.throws(() => computeMatches(data), DuplicateEpisodeError);

// Explicit canonical nulls take precedence over legacy aliases at the boundary.
const explicitNull = normalizeTorrentPreview({ parsed_files: [{ file_name: 'Unknown.mkv', season: 1, episode: 3,
  parsed_episode: { season_number: null, episode_number: null } }] });
assert.deepEqual(explicitNull.parsed_files[0].parsed, { season_number: null, episode_number: null });
const unknownId = normalizeEpisodeCatalog({ tmdb: { 100: { 1: { episodes: [{ epNum: 3, episode_id: null, tmdbId: 999 }] } } } });
assert.equal(unknownId.tmdb[100][1].episodes[0].episode_id, null);
// Download carries the exact manual mapping and provider IDs alongside the legacy bridge.
const submitted = { mapping: row.mapping };
assert.deepEqual(submitted.mapping, row.mapping);
assert.equal(submitted.mapping.tmdb.series_id, 100);
assert.equal(submitted.mapping.bangumi.episode_number, 9);
assert.equal(submitted.mapping.bangumi.episode_absolute, 16);
assert.ok(!('title' in submitted.mapping));
console.log('Episode matching: canonical coordinates, identity, overrides, compatibility and duplicate scenarios passed');
