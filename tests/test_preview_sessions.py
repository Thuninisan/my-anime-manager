"""Preview persistence, projection, and download boundary regressions."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock, Mock

from fastapi import HTTPException
from backend.db import connection, preview_sessions as repository
from backend.domain.episode import create_episode_mapping
from backend.services.torrent import preview_session as service
from backend.services.torrent.preview_view import session_view


class PreviewSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(connection, 'DB_PATH', self.root / 'db.sqlite3'),
                        patch.object(service, 'PREVIEW_DIR', self.root / 'previews'),
                        patch.object(service.config, 'PREVIEW_SESSION_TTL_HOURS', 72)]
        for item in self.patches:
            item.start()
        self.source = self.root / 'input.torrent'
        self.source.write_bytes(b'torrent source')
        self.result = {'torrent_name': 'Test', 'index': 'tvdb',
                       'parsed_files': [{'file_name': 'a.mkv', 'torrent_path': 'a.mkv', 'show_name': 'A', 'season': 0, 'episode': 0},
                                        {'file_name': 'b.mkv', 'torrent_path': 'b.mkv', 'show_name': 'B', 'season': 1, 'episode': 1}],
                       'subtitles': ['a.ass'],
                       'search_results': {'A': {'tmdb': {'id': 1, 'name': 'A'}, 'bangumi': {'id': 2, 'name': 'A'}},
                                          'B': {'tmdb': {'id': 3, 'name': 'B'}, 'bangumi': None}},
                       'provider_catalogs': {'tmdb': {'1': {'0': {'name': 'Specials', 'episodes': [
                           {'tmdbId': 11, 'epNum': 0, 'name': 'zero', 'overview': 'hidden plot', 'voteAverage': 0,
                            'guestStars': [{'name': 'Guest'}], 'directors': ['Director'], 'writers': ['Writer']}]}},
                                                '3': {'1': {'name': 'Season', 'episodes': [{'tmdbId': 31, 'epNum': 1, 'name': 'B'}]}}},
                                        'bangumi': {'2': {'name': 'A', 'episodes': [{'id': 21, 'ep': 0, 'sort': 0, 'raw_sort': 0, 'name': 'A'}]}}}}
        self.row = service.create_preview_session(self.result, str(self.source))
        self.row, self.snapshot = service.load_preview_session(self.row.id)
        self.mapping = create_episode_mapping({'season_number': 0, 'episode_number': 0},
            {'subject_id': 2, 'episode_id': 21, 'episode_number': 0, 'episode_absolute': 0},
            {'series_id': 1, 'episode_id': 11, 'season_number': 0, 'episode_number': 0})

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_round_trip_and_projection(self):
        self.assertEqual(self.snapshot['episode_metadata']['tmdb:11']['rating'], 0)
        self.assertEqual(self.snapshot['episode_catalog']['tmdb']['1']['0']['episodes'][0]['season_number'], 0)
        self.assertEqual(self.snapshot['episode_catalog']['bangumi']['2']['episodes'][0]['episode_absolute'], 0)
        self.assertEqual(service.metadata_candidates(self.snapshot, self.mapping)['tmdb']['plot'], 'hidden plot')
        view = session_view(self.row)
        wire = json.dumps(view)
        for field in ('overview', 'guest_stars', 'directors', 'writers', 'hidden plot', 'context_json', 'source_path'):
            self.assertNotIn(field, wire)
        self.assertEqual(view['parsed_files'][0]['file_id'], service.file_id('a.mkv'))
        self.assertTrue(Path(self.snapshot['torrent']['source_path']).is_file())
        self.assertEqual(service.file_id('a/../a.mkv'), service.file_id('a.mkv'))

    def request(self):
        return {'preview_id': self.row.id, 'preview_revision': 1, 'files': [
            {'file_id': service.file_id('a.mkv'), 'mapping': copy.deepcopy(self.mapping)}]}

    def test_restore_and_manual_override(self):
        request = self.request()
        restored = service.restore_download_request(request)
        self.assertEqual(restored['files'][0]['torrent_path'], 'a.mkv')
        self.assertIn('episode_metadata', restored['preview_snapshot'])
        request['files'][0]['mapping']['tmdb'].update(episode_id=None, episode_number=99)
        restored = service.restore_download_request(request)
        self.assertIsNone(restored['files'][0]['episode_mapping']['tmdb']['episode_id'])

    def test_invalid_download(self):
        for field, value, code, detail in [('preview_id', 'unknown', 404, 'preview_not_found'),
                                          ('preview_revision', 2, 409, 'preview_revision_conflict')]:
            body = self.request()
            body[field] = value
            with self.assertRaises(HTTPException) as error:
                service.restore_download_request(body)
            self.assertEqual((error.exception.status_code, error.exception.detail), (code, detail))
        for mutation in ('file', 'episode', 'series', 'malformed'):
            body = self.request()
            if mutation == 'file': body['files'][0]['file_id'] = 'unknown'
            elif mutation == 'episode': body['files'][0]['mapping']['tmdb']['episode_id'] = 31
            elif mutation == 'series': body['files'][0]['mapping']['tmdb']['series_id'] = 9
            else: body['files'][0]['mapping'] = {}
            with self.assertRaises(HTTPException) as error:
                service.restore_download_request(body)
            self.assertEqual(error.exception.status_code, 422)

    def test_update_schema_expiration_cleanup(self):
        updated = service.update_preview_session(self.row, self.snapshot)
        self.assertEqual(updated.revision, 2)
        with self.assertRaises(HTTPException):
            service.update_preview_session(self.row, self.snapshot)
        repository.update(updated.id, 2, schema_version=99)
        with self.assertRaises(HTTPException) as error:
            service.load_preview_session(updated.id)
        self.assertEqual(error.exception.detail, 'preview_schema_outdated')
        repository.update(updated.id, 2, expires_at='2000-01-01T00:00:00+00:00')
        with self.assertRaises(HTTPException) as error:
            service.load_preview_session(updated.id)
        self.assertEqual(error.exception.status_code, 410)
        self.assertEqual(service.cleanup_expired_preview_sessions(), 1)
        self.assertIsNone(repository.get(updated.id))
        self.assertFalse(Path(self.snapshot['torrent']['source_path']).exists())

    def test_multi_series(self):
        body = self.request()
        mapping = create_episode_mapping({'season_number': 1, 'episode_number': 1}, tmdb={
            'series_id': 3, 'episode_id': 31, 'season_number': 1, 'episode_number': 1})
        body['files'].append({'file_id': service.file_id('b.mkv'), 'mapping': mapping})
        restored = service.restore_download_request(body)
        self.assertEqual([f['tmdb_show_name'] for f in restored['files']], ['A', 'B'])

    def test_unparsed_special_directories_create_valid_session(self):
        from backend.services.torrent.preview import _parse_file
        from backend.domain.episode_adapters import parsed_episode_ref
        # Reproduce the 34 files skipped before anitopy assigns a show name.
        skipped = [_parse_file({'name': f'Show/SPs/extra-{i}.mkv'}) for i in range(34)]
        self.assertTrue(all(item['show_name'] is None for item in skipped))
        self.result['specials'] = [{
            'file_name': item['file_name'], 'torrent_path': item['torrent_path'],
            'show_name': item['show_name'], 'parsed_episode': parsed_episode_ref(item),
        } for item in skipped]
        row = service.create_preview_session(self.result, str(self.source))
        _, snapshot = service.load_preview_session(row.id)
        view = session_view(row)
        specials = [item for item in snapshot['parsed_files'] if item['kind'] == 'special']
        self.assertEqual(len(specials), 34)
        self.assertTrue(all(item['show_key'] == '' for item in specials))
        self.assertEqual(len(view['specials']), 34)
        self.assertTrue(all(item['show_name'] == '' for item in view['specials']))
        self.assertEqual(snapshot['series_contexts'], self.snapshot['series_contexts'])
        self.assertEqual([item['show_name'] for item in view['parsed_files']], ['A', 'B'])

    def test_unknown_show_name_does_not_mask_invalid_types(self):
        from pydantic import ValidationError
        self.result['specials'] = [{'file_name': 'bad.mkv', 'torrent_path': 'SPs/bad.mkv',
                                   'show_name': 123}]
        with self.assertRaises(ValidationError):
            service.create_preview_session(self.result, str(self.source))

    def test_ambiguity_round_trip_and_manual_multiseason_confirmation(self):
        import asyncio
        from backend.services.torrent.preview import _preview_provider_result, _combine_preview_resolutions
        _, tmdb = _preview_provider_result('tmdb', [{'id': 1, 'name': 'A'}], 'A')
        _, bgm = _preview_provider_result('bangumi', [{'id': 2, 'name': 'Season 1'}, {'id': 4, 'name': 'Season 2'}], 'A')
        resolutions = {'tmdb': tmdb, 'bangumi': bgm}
        self.result['search_results']['A'].update(bangumi=None, provider_resolutions=resolutions,
            resource_resolution=_combine_preview_resolutions(resolutions, 'A', 'tv'))
        row = service.create_preview_session(self.result, str(self.source))
        before = session_view(row)
        self.assertEqual(before['search_results']['A']['provider_resolutions']['bangumi']['status'], 'ambiguous')
        self.assertEqual(before['search_results']['A']['resource_identity']['tmdb_series_id'], 1)
        self.assertIsNone(before['search_results']['A']['bangumi_subject_id'])
        async def run():
            for revision, bid in ((1, 2), (2, 4)):
                with patch('backend.clients.bangumi.get_subject', AsyncMock(return_value={'name': str(bid)})), \
                     patch('backend.clients.bangumi.get_episodes', AsyncMock(return_value=[{'id': bid * 10 + 1, 'sort': 0, 'ep': 0, 'type': 0}])):
                    view = await service.augment_preview_session(row.id, revision, 'A', 'bangumi', bid)
                self.assertEqual(view['revision'], revision + 1)
                self.assertEqual(view['search_results']['B'], before['search_results']['B'])
            self.assertEqual(view['search_results']['A']['bangumi_subject_ids'], [2, 4])
            self.assertEqual(view['search_results']['A']['resource_identity']['bangumi_subject_id'], 4)
            self.assertEqual(view['search_results']['A']['provider_resolutions']['bangumi']['status'], 'resolved')
            self.assertIn('2', view['episode_catalog']['bangumi'])
            self.assertIn('4', view['episode_catalog']['bangumi'])
            request = {'preview_id': row.id, 'preview_revision': 3, 'files': [
                {'file_id': service.file_id('a.mkv'), 'mapping': self.mapping}]}
            restored = service.restore_download_request(request)
            self.assertEqual(restored['files'][0]['episode_mapping']['bangumi']['subject_id'], 2)
            self.assertEqual(restored['files'][0]['resource_identity']['bangumi_subject_id'], 4)
            request['files'][0]['file_id'] = service.file_id('b.mkv')
            with self.assertRaises(HTTPException) as error:
                service.restore_download_request(request)
            self.assertEqual(error.exception.detail, 'invalid_resource_identity')
        asyncio.run(run())

    def test_augment_all_providers(self):
        import asyncio
        async def run():
            with patch('backend.services.tmdb.build_season_episode_map', AsyncMock(return_value={0: {'name': 'new', 'episodes': [
                    {'tmdbId': 91, 'epNum': 0, 'name': 'New', 'overview': 'private'}]}})):
                view = await service.augment_preview_session(self.row.id, 1, 'A', 'tmdb', 9)
                self.assertEqual(view['revision'], 2)
                self.assertIn('1', view['episode_catalog']['tmdb'])
            with patch('backend.services.tvdb.fetch_tvdb_series_episodes', AsyncMock(return_value={'name': 'TV', 'seasons': {0: {
                    'name': 'Special', 'episodes': [{'tvdbId': 81, 'epNum': 0, 'name': 'TV'}]}}})):
                view = await service.augment_preview_session(self.row.id, 2, 'A', 'tvdb', 8)
                self.assertEqual(view['revision'], 3)
            with patch('backend.clients.bangumi.get_subject', AsyncMock(return_value={'name': 'BG'})), patch(
                    'backend.clients.bangumi.get_episodes', AsyncMock(return_value=[{'id': 71, 'ep': 0, 'sort': 0, 'type': 1, 'name': 'BG'}])):
                view = await service.augment_preview_session(self.row.id, 3, 'A', 'bangumi', 7)
                self.assertEqual(view['revision'], 4)
            _, snapshot = service.load_preview_session(self.row.id)
            self.assertEqual(snapshot['episode_metadata']['tmdb:91']['plot'], 'private')
            self.assertIn('tvdb:81', snapshot['episode_metadata'])
            self.assertIn('bangumi:71', snapshot['episode_metadata'])
        asyncio.run(run())


    def test_canonical_snapshot_nfo_uses_no_catalog_requests(self):
        import asyncio
        from backend.services.torrent.metadata import pre_generate_nfo
        async def run():
            snapshot = copy.deepcopy(self.snapshot)
            snapshot['episode_metadata']['tmdb:11']['plot'] = '这是本集中文简介。'
            snapshot['episode_metadata']['bangumi:21']['title'] = '中文标题'
            row = service.update_preview_session(self.row, snapshot)
            body = self.request()
            body['preview_revision'] = row.revision
            restored = service.restore_download_request(body)
            with patch('backend.services.tmdb.build_season_episode_map', AsyncMock(side_effect=AssertionError('duplicate TMDB catalog'))) as tmdb, \
                 patch('backend.services.tvdb.fetch_tvdb_series_episodes', AsyncMock(side_effect=AssertionError('duplicate TVDB catalog'))) as tvdb, \
                 patch('backend.services.enrich._get_bangumi_episodes', AsyncMock(side_effect=AssertionError('duplicate Bangumi catalog'))) as bangumi, \
                 patch('backend.clients.tmdb.get_tv_detail', AsyncMock()) as detail, \
                 patch('backend.clients.bangumi.get_subject', AsyncMock(return_value={'name_cn': 'A'})), \
                 patch('backend.services.nfo.images.download_show_images', AsyncMock(return_value={})), \
                 patch.object(service.config, 'RSS_PATH_TEMPLATE', '{series_name}/Season {tmdb_season:02d}/{tmdb_title} {tmdb_episode:02d}'):
                detail.return_value = Mock()
                detail.return_value.json.return_value = {'name': 'A', 'overview': '作品中文简介。'}
                result = await pre_generate_nfo(restored['preview_snapshot'], restored['files'], 'Test', str(self.root / 'nfo'), 'A')
                self.assertTrue(result[1])
                tmdb.assert_not_awaited()
                tvdb.assert_not_awaited()
                bangumi.assert_not_awaited()
        asyncio.run(run())

    def test_snapshot_round_trip_preserves_seven_xml_goldens(self):
        import xml.etree.ElementTree as ET
        from tests.test_episode_metadata import candidates, mapping
        from backend.services.episode_metadata_resolver import resolve_episode
        from backend.services.nfo.nfo_xml import generate_episode_nfo
        cases = {'regular': {}, 'special': {'season_number': 0},
                 'missing_tmdb': {'no_tmdb': True}, 'missing_tvdb': {'no_tvdb': True},
                 'bangumi_only': {'no_tmdb': True, 'no_tvdb': True},
                 'manual_override': {'season_number': 2, 'episode_number': 15}, 'guests': {}}
        row = self.row
        for case, options in cases.items():
            available = candidates()
            available['tmdb']['still_path'] = 'episode.jpg'
            if options.get('no_tmdb'): available['tmdb'] = None
            if options.get('no_tvdb'): available['tvdb'] = None
            if available['tvdb'] and not options.get('no_tmdb'): available['tvdb']['rating'] = 8.2
            snapshot = copy.deepcopy(self.snapshot)
            snapshot['episode_metadata'] = {f'{provider}:{metadata["provider_episode_id"]}': metadata
                                            for provider, metadata in available.items() if metadata is not None}
            row = service.update_preview_session(row, snapshot)
            _, snapshot = service.load_preview_session(row.id)
            ref = mapping(options.get('season_number', 1), options.get('episode_number', 3))
            if options.get('no_tvdb'): ref['tvdb']['episode_id'] = 0
            resolved = resolve_episode(ref, service.metadata_candidates(snapshot, ref))
            if case == 'bangumi_only': resolved['metadata']['plot'] = '翻译后的简介。'
            written = generate_episode_nfo(resolved, show_name='测试番剧 & Show', bangumi_subject_name='作品原名',
                thumb_path='' if case == 'bangumi_only' else 'episode.jpg', output_dir=str(self.root), file_stem=case)
            actual = ET.canonicalize(Path(written).read_text(), strip_text=True)
            expected = ET.canonicalize((Path(__file__).parent / 'fixtures' / 'episode_nfo' / f'{case}.xml').read_text(), strip_text=True)
            self.assertEqual(actual, expected, case)

    def test_localized_fallback_requests_only_selected_episode(self):
        import asyncio
        from backend.services.episode_metadata_resolver import resolve_nfo_episode
        from backend.services.nfo.metadata_context import MetadataContext
        async def run():
            snapshot = copy.deepcopy(self.snapshot)
            snapshot['episode_metadata']['tvdb:41'] = copy.deepcopy(snapshot['episode_metadata']['tmdb:11'])
            snapshot['episode_metadata']['tvdb:41'].update(provider='tvdb', provider_episode_id=41)
            mapping = copy.deepcopy(self.mapping)
            mapping['tvdb'] = {'series_id': 4, 'episode_id': 41, 'season_number': 0, 'episode_number': 0}
            snapshot['episode_metadata']['bangumi:21']['title'] = '中文标题'
            ctx = MetadataContext()
            ctx.preview_snapshot = snapshot
            candidates = service.metadata_candidates(snapshot, mapping)
            with patch('backend.clients.tvdb.get_series_episodes', AsyncMock(side_effect=AssertionError('whole catalog'))) as catalog, \
                 patch('backend.clients.tvdb.get_episode_translations', AsyncMock()) as translation:
                translation.return_value = Mock()
                translation.return_value.json.return_value = {'data': {'overview': '这是单集中文简介。'}}
                resolved = await resolve_nfo_episode(mapping, candidates, metadata_ctx=ctx)
                self.assertEqual(resolved['metadata']['plot'], '这是单集中文简介。')
                translation.assert_awaited_once_with(41, 'zho')
                catalog.assert_not_awaited()
        asyncio.run(run())

    def test_api_round_trip_and_rejections_before_download(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.api.routes_torrent import router
        app = FastAPI()
        app.include_router(router)
        with TestClient(app) as client:
            response = client.get(f'/api/torrent/previews/{self.row.id}')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['preview_id'], self.row.id)
            self.assertEqual(client.get('/api/torrent/previews/missing').status_code, 404)
            for body in ({'preview_id': self.row.id},
                         dict(self.request(), files=[{'file_id': service.file_id('a.mkv'), 'mapping': {}}]),
                         dict(self.request(), preview_revision=99)):
                with patch('backend.api.routes_torrent.qb_login', AsyncMock()) as login:
                    response = client.post('/api/torrent/download', json=body)
                    self.assertIn(response.status_code, (422, 409))
                    login.assert_not_awaited()
            captured = []
            async def parse(path):
                captured.append(path)
                return copy.deepcopy(self.result)
            with patch('backend.services.torrent.preview.parse_and_search', parse):
                response = client.post('/api/torrent/parse-and-search', files={'file': ('new.torrent', b'torrent source')})
            self.assertEqual(response.status_code, 200)
            view = response.json()
            self.assertNotIn('provider_catalogs', view)
            self.assertNotIn('hidden plot', response.text)
            self.assertFalse(Path(captured[0]).exists())
            _, snapshot = service.load_preview_session(view['preview_id'])
            self.assertTrue(Path(snapshot['torrent']['source_path']).exists())

    def test_source_and_idless_coordinate_metadata(self):
        raw = copy.deepcopy(self.result)
        raw['provider_catalogs']['tmdb']['1']['0']['episodes'][0]['tmdbId'] = None
        snapshot = service.build_snapshot(raw, self.source)
        mapping = copy.deepcopy(self.mapping)
        mapping['tmdb']['episode_id'] = None
        mapping['tmdb']['episode_number'] = 0.0
        self.assertEqual(service.metadata_candidates(snapshot, mapping)['tmdb']['plot'], 'hidden plot')
        source = Path(self.snapshot['torrent']['source_path'])
        source.write_bytes(b'changed')
        with self.assertRaises(HTTPException) as error:
            service.restore_download_request(self.request())
        self.assertEqual(error.exception.detail, 'preview_source_changed')
        source.unlink()
        with self.assertRaises(HTTPException) as error:
            service.restore_download_request(self.request())
        self.assertEqual(error.exception.detail, 'preview_source_missing')

    def test_movie_identity_without_episode_catalog(self):
        result = copy.deepcopy(self.result)
        result['search_results']['A']['media_type'] = 'movie'
        result['provider_catalogs'] = {}
        row = service.create_preview_session(result, str(self.source))
        mapping = create_episode_mapping({'season_number': None, 'episode_number': None},
            {'subject_id': 2, 'episode_id': None, 'episode_number': None, 'episode_absolute': None})
        restored = service.restore_download_request({'preview_id': row.id, 'preview_revision': 1,
            'files': [{'file_id': service.file_id('a.mkv'), 'mapping': mapping}]})
        self.assertEqual(restored['files'][0]['bangumi_show_name'], 'A')
        self.assertEqual(restored['preview_snapshot']['series_contexts']['A']['resource_identity']['tmdb_movie_id'], 1)

    def test_failed_insert_and_augment_leave_no_partial_context(self):
        import asyncio
        directories = set(service.PREVIEW_DIR.iterdir())
        with patch.object(repository, 'create', side_effect=RuntimeError('DB insert failed')):
            with self.assertRaises(RuntimeError):
                service.create_preview_session(self.result, str(self.source))
        self.assertEqual(set(service.PREVIEW_DIR.iterdir()), directories)
        async def run():
            with patch('backend.services.tmdb.build_season_episode_map', AsyncMock(side_effect=RuntimeError('provider offline'))):
                with self.assertRaises(RuntimeError):
                    await service.augment_preview_session(self.row.id, 1, 'A', 'tmdb', 9)
        asyncio.run(run())
        row, snapshot = service.load_preview_session(self.row.id)
        self.assertEqual(row.revision, 1)
        self.assertEqual(snapshot, self.snapshot)
        repository.delete(row.id)
        self.assertIsNone(repository.get(row.id))

    def test_canonical_resource_context_and_cross_series_rejection(self):
        self.assertEqual(self.snapshot['series_contexts']['A']['resource_identity']['tmdb_series_id'], 1)
        self.assertEqual(self.snapshot['series_contexts']['B']['resource_identity']['tmdb_series_id'], 3)
        request = self.request()
        request['files'][0]['mapping']['tmdb'].update(series_id=3, episode_id=31, season_number=1, episode_number=1)
        with self.assertRaises(HTTPException) as error:
            service.restore_download_request(request)
        self.assertEqual(error.exception.detail, 'invalid_resource_identity')

    def test_augment_updates_identity_and_only_target_context(self):
        import asyncio
        async def run():
            with patch('backend.services.tmdb.build_season_episode_map', AsyncMock(return_value={1: {'episodes': []}})):
                await service.augment_preview_session(self.row.id, 1, 'B', 'tmdb', 99)
        asyncio.run(run())
        _, snapshot = service.load_preview_session(self.row.id)
        self.assertEqual(snapshot['series_contexts']['A']['resource_identity']['tmdb_series_id'], 1)
        self.assertEqual(snapshot['series_contexts']['B']['resource_identity']['tmdb_series_id'], 99)
        self.assertEqual(snapshot['series_contexts']['B']['resource_resolution']['reason'], 'manual_provider_confirmation')

    def test_movie_identity_does_not_use_canonical_series_field(self):
        result = copy.deepcopy(self.result)
        result['search_results'] = {'A': {'tmdb': {'id': 99, 'name': 'Movie'}, 'media_type': 'movie'}}
        snapshot = service.build_snapshot(result, self.source)
        identity = snapshot['series_contexts']['A']['resource_identity']
        self.assertIsNone(identity['tmdb_series_id'])
        self.assertEqual(identity['tmdb_movie_id'], 99)

    def test_conflicting_mapping_hints_do_not_pick_first(self):
        result = copy.deepcopy(self.result)
        result['search_results']['A']['map_entries'] = [{'tvdb_id': 10}, {'tvdb_id': 20}]
        with self.assertRaisesRegex(ValueError, 'ambiguous_resource'):
            service.build_snapshot(result, self.source)
