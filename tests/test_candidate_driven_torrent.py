"""Phase 8: candidates are evidence; submitted per-file mappings own identity."""
import asyncio
import copy
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from backend.domain.episode import create_episode_mapping
from backend.domain.persistence import episode_mapping_snapshot, load_episode_mapping_snapshot
from backend.domain.resource_adapters import provider_candidates
from backend.services.torrent import preview_session as service
from backend.services.torrent.preview_view import session_view
from tests import test_preview_sessions as fixtures


class CandidateDrivenTorrentTests(unittest.TestCase):
    setUp = fixtures.PreviewSessionTests.setUp
    tearDown = fixtures.PreviewSessionTests.tearDown
    request = fixtures.PreviewSessionTests.request

    def update(self, snapshot):
        self.row = service.update_preview_session(self.row, snapshot)
        return self.row.revision

    def restore(self, body=None):
        body = body or self.request()
        body['preview_revision'] = self.row.revision
        return service.restore_download_request(body)

    def assert_rejected(self, body, detail=None):
        with self.assertRaises(HTTPException) as error:
            self.restore(body)
        self.assertEqual(error.exception.status_code, 422)
        if detail:
            self.assertEqual(error.exception.detail, detail)

    def add_tvdb(self, snapshot):
        snapshot['series_contexts']['A']['candidates']['tvdb'] = provider_candidates('tvdb', [{'id': 8, 'name': 'A'}])
        snapshot['episode_catalog']['tvdb']['8'] = {'name': 'A', 'seasons': {'2': {'name': 'S2', 'episodes': [
            {'series_id': 8, 'episode_id': 81, 'season_number': 2, 'episode_number': 1, 'episode_absolute': 1, 'name': 'zero'}]}}}

    def test_unique_candidate_never_confirms_identity(self):
        wire = session_view(self.row)
        entry = wire['search_results']['A']
        self.assertEqual([c['provider_id'] for c in entry['candidates']['tmdb']], [1])
        for forbidden in ('resource_identity', 'resource_resolution', 'provider_resolutions', 'tmdb_series_id',
                          'bangumi_subject_id', 'bangumi_subject_ids'):
            self.assertNotIn(forbidden, entry)
            self.assertNotIn(forbidden, self.snapshot['series_contexts']['A'])
        self.assertNotIn('series', wire)
        self.assertNotIn('resource_candidates', wire)

    def test_removal_invalidates_mapping_but_retains_cache(self):
        view = service.remove_preview_candidate(self.row.id, 1, 'A', 'bangumi', 2)
        self.row, snapshot = service.load_preview_session(self.row.id)
        self.assertEqual(view['revision'], 2)
        self.assertIn('2', snapshot['episode_catalog']['bangumi'])
        self.assertEqual(snapshot['series_contexts']['A']['candidates']['bangumi'], [])
        self.assert_rejected(self.request(), 'invalid_resource_identity')
        self.assertEqual(snapshot['series_contexts']['B'], self.snapshot['series_contexts']['B'])

    def test_removed_candidate_can_reuse_cached_directory(self):
        service.remove_preview_candidate(self.row.id, 1, 'A', 'bangumi', 2)
        with patch('backend.clients.bangumi.get_subject', AsyncMock(return_value={'id': 2, 'name': 'A'})), \
             patch('backend.clients.bangumi.get_episodes', AsyncMock(side_effect=AssertionError('cached'))) as fetch:
            view = asyncio.run(service.augment_preview_session(self.row.id, 2, 'A', 'bangumi', 2))
        fetch.assert_not_awaited()
        self.row, _ = service.load_preview_session(self.row.id)
        self.assertEqual(view['revision'], 3)
        self.assertEqual(self.restore()['files'][0]['resource_identity']['bangumi_subject_id'], 2)

    def test_duplicate_augment_reuses_details_and_episode_cache(self):
        with patch('backend.clients.bangumi.get_subject', AsyncMock(side_effect=AssertionError('cached'))) as detail, \
             patch('backend.clients.bangumi.get_episodes', AsyncMock(side_effect=AssertionError('cached'))) as fetch:
            view = asyncio.run(service.augment_preview_session(self.row.id, 1, 'A', 'bangumi', 2))
        detail.assert_not_awaited()
        fetch.assert_not_awaited()
        self.assertEqual(len(view['search_results']['A']['candidates']['bangumi']), 1)
        self.assertNotIn('resource_identity', view['search_results']['A'])

    def test_scope_is_not_inferred_from_shared_catalog_cache(self):
        body = self.request()
        body['files'][0]['mapping']['tmdb'].update(series_id=3, episode_id=31, season_number=1, episode_number=1)
        self.assert_rejected(body, 'invalid_resource_identity')

    def test_candidate_without_loaded_directory_is_rejected(self):
        snapshot = copy.deepcopy(self.snapshot)
        snapshot['series_contexts']['A']['candidates']['tmdb'].extend(provider_candidates('tmdb', [{'id': 99}]))
        self.update(snapshot)
        body = self.request()
        body['files'][0]['mapping']['tmdb']['series_id'] = 99
        self.assert_rejected(body, 'invalid_episode_mapping')

    def test_episode_id_does_not_authorize_wrong_coordinates(self):
        for provider, field, value in [('tmdb', 'season_number', 1), ('tmdb', 'episode_number', 99),
                                       ('bangumi', 'episode_number', 2), ('bangumi', 'episode_absolute', 2)]:
            with self.subTest(provider=provider, field=field):
                body = self.request()
                body['files'][0]['mapping'][provider][field] = value
                self.assert_rejected(body, 'invalid_episode_mapping')

    def test_coordinate_only_reference_must_exist(self):
        body = self.request()
        body['files'][0]['mapping']['tmdb']['episode_id'] = None
        self.assertEqual(self.restore(body)['files'][0]['episode_mapping']['tmdb']['episode_number'], 0)
        body['files'][0]['mapping']['tmdb']['episode_number'] = 99
        self.assert_rejected(body, 'invalid_episode_mapping')

    def test_null_series_cannot_hide_non_null_episode(self):
        body = self.request()
        body['files'][0]['mapping']['tmdb']['series_id'] = None
        self.assert_rejected(body, 'invalid_episode_mapping')

    def test_final_identity_never_falls_back_to_recommended_tmdb(self):
        snapshot = copy.deepcopy(self.snapshot)
        self.add_tvdb(snapshot)
        snapshot['episode_match_source'] = 'tvdb'
        self.update(snapshot)
        body = self.request()
        mapping = body['files'][0]['mapping']
        mapping['match_source'] = 'tvdb'
        mapping['tmdb'] = create_episode_mapping(mapping['parsed'])['tmdb']
        mapping['tvdb'] = {'series_id': 8, 'episode_id': 81, 'season_number': 2, 'episode_number': 1}
        restored = self.restore(body)['files'][0]
        self.assertIsNone(restored['resource_identity']['tmdb_series_id'])
        self.assertEqual(restored['resource_identity']['tvdb_series_id'], 8)
        self.assertEqual(restored['episode_mapping'], mapping)

    def test_tvdb_coordinates_are_validated_against_the_episode(self):
        snapshot = copy.deepcopy(self.snapshot)
        self.add_tvdb(snapshot)
        self.update(snapshot)
        body = self.request()
        body['files'][0]['mapping']['tvdb'] = {'series_id': 8, 'episode_id': 81, 'season_number': 2, 'episode_number': 99}
        self.assert_rejected(body, 'invalid_episode_mapping')

    def test_mapping_source_cannot_be_null_or_stale(self):
        for source in (None, 'tvdb'):
            body = self.request()
            body['files'][0]['mapping']['match_source'] = source
            with self.assertRaises(HTTPException) as error:
                self.restore(body)
            self.assertEqual(error.exception.detail, 'episode_match_source_conflict')

    def test_multiseason_files_persist_different_subjects_for_one_tmdb(self):
        result = copy.deepcopy(self.result)
        result['parsed_files'][1]['show_name'] = 'A'
        result['search_results']['A']['bangumi_ids'] = [2, 4]
        result['provider_catalogs']['bangumi']['4'] = {'name': 'Second season', 'episodes': [{'id': 41, 'ep': 1, 'sort': 1, 'name': 'second'}]}
        result['provider_catalogs']['tmdb']['1']['2'] = {'name': 'Second season', 'episodes': [{'tmdbId': 12, 'epNum': 1, 'name': 'second'}]}
        self.row = service.create_preview_session(result, str(self.source))
        body = self.request()
        second = create_episode_mapping({'season_number': 1, 'episode_number': 1},
            {'subject_id': 4, 'episode_id': 41, 'episode_number': 1, 'episode_absolute': 1},
            {'series_id': 1, 'episode_id': 12, 'season_number': 2, 'episode_number': 1}, match_source='tmdb')
        body['files'].append({'file_id': service.file_id('b.mkv'), 'mapping': second})
        files = self.restore(body)['files']
        snapshots = [episode_mapping_snapshot(f['episode_mapping'], f['resource_identity']) for f in files]
        self.assertEqual([s['resource_identity']['bangumi_subject_id'] for s in snapshots], [2, 4])
        self.assertEqual([s['resource_identity']['tmdb_series_id'] for s in snapshots], [1, 1])
        self.assertEqual(snapshots[1]['episode_mapping']['tmdb']['season_number'], 2)
        self.assertEqual(load_episode_mapping_snapshot(json.dumps(snapshots[1])), snapshots[1])

    def test_subtitles_follow_validated_video_mapping(self):
        body = self.request()
        body['files'].append({'file_id': service.file_id('a.ass'), 'mapping': copy.deepcopy(self.mapping), 'subtitle_suffix': '.zh.ass'})
        body['uploaded_subtitles'] = [{'file_id': service.file_id('a.mkv'), 'mapping': copy.deepcopy(self.mapping),
                                      'stored_filename': 'stored.ass', 'original_filename': 'a.ass'}]
        restored = self.restore(body)
        self.assertTrue(restored['files'][1]['is_subtitle'])
        self.assertEqual(restored['files'][1]['resource_identity'], restored['files'][0]['resource_identity'])
        self.assertEqual(restored['uploaded_subtitles'][0]['episode_mapping'], self.mapping)
        body['files'][1]['mapping']['tmdb'].update(series_id=3, episode_id=31, season_number=1, episode_number=1)
        self.assert_rejected(body)

    def movie_body(self):
        result = copy.deepcopy(self.result)
        result['search_results']['A']['media_type'] = 'movie'
        result['provider_catalogs'] = {}
        self.row = service.create_preview_session(result, str(self.source))
        return {'preview_id': self.row.id, 'preview_revision': 1, 'files': [{'file_id': service.file_id('a.mkv'),
            'tmdb_movie_id': 1, 'mapping': create_episode_mapping({'season_number': None, 'episode_number': None},
                {'subject_id': 2, 'episode_id': None, 'episode_number': None, 'episode_absolute': None}, match_source='tmdb')}]}

    def test_movie_reference_is_explicit_and_scoped(self):
        body = self.movie_body()
        valid = copy.deepcopy(body)
        del body['files'][0]['tmdb_movie_id']
        self.assert_rejected(body, 'invalid_movie_candidate')
        body['files'][0]['tmdb_movie_id'] = 3
        self.assert_rejected(body, 'invalid_movie_candidate')
        restored = self.restore(valid)['files'][0]
        self.assertEqual(restored['resource_identity']['tmdb_movie_id'], 1)
        self.assertIsNone(restored['resource_identity']['tmdb_series_id'])
        self.assertIsNone(restored['episode_mapping']['tmdb']['series_id'])

    def test_movie_nfo_uses_submitted_identity_and_processing_keeps_snapshot(self):
        from backend.services.torrent.metadata import pre_generate_nfo
        from backend.services.torrent.monitor import build_processing
        restored = self.restore(self.movie_body())
        with patch.object(service.config, 'MOVIE_HARDLINK_PATH', str(self.root / 'movies')), \
             patch('backend.services.nfo.nfo_xml.generate_movie_nfo', Mock(return_value='movie.nfo')) as generate:
            movie, generated, metadata = asyncio.run(pre_generate_nfo(restored['preview_snapshot'], restored['files'], 'Test', str(self.root), ''))
        self.assertTrue(movie and generated)
        self.assertEqual(generate.call_args.kwargs['tmdb_id'], 1)
        self.assertEqual(generate.call_args.kwargs['bangumi_id'], 2)
        processing = build_processing(dict(restored, hardlink_root=str(self.root), movie_meta=metadata))
        snapshot = processing['files'][0]['episode_mapping_snapshot']
        self.assertEqual(snapshot['resource_identity']['tmdb_movie_id'], 1)
        self.assertEqual(load_episode_mapping_snapshot(snapshot), snapshot)

    def test_augment_does_not_edit_subscriptions(self):
        from backend import data
        with patch.object(data, 'update_subscription', side_effect=AssertionError('must not edit RSS')) as update, \
             patch('backend.clients.bangumi.get_subject', AsyncMock(return_value={'id': 7, 'name': 'Second'})), \
             patch('backend.clients.bangumi.get_episodes', AsyncMock(return_value=[{'id': 71, 'ep': 1, 'sort': 1, 'type': 0}])):
            view = asyncio.run(service.augment_preview_session(self.row.id, 1, 'A', 'bangumi', 7))
        update.assert_not_called()
        self.assertEqual({c['provider_id'] for c in view['search_results']['A']['candidates']['bangumi']}, {2, 7})
        self.assertNotIn('resource_identity', view['search_results']['A'])

    def test_invalid_tmdb_detail_does_not_modify_session(self):
        with patch('backend.clients.tmdb.get_tv_detail', AsyncMock(return_value=Mock(json=lambda: {'id': 100}))):
            with self.assertRaises(HTTPException):
                asyncio.run(service.augment_preview_session(self.row.id, 1, 'A', 'tmdb', 99))
        row, snapshot = service.load_preview_session(self.row.id)
        self.assertEqual(row.revision, 1)
        self.assertEqual(snapshot, self.snapshot)

    def test_unrelated_augmentation_does_not_readd_removed_candidate(self):
        service.remove_preview_candidate(self.row.id, 1, 'A', 'bangumi', 2)
        with patch('backend.data.get_map_entries_by_tmdb_id', return_value=[{'bangumi_id': 2, 'name': 'A', 'tmdb_season': 1}]):
            view = asyncio.run(service.augment_preview_session(self.row.id, 2, 'A', 'tmdb', 1))
        self.assertEqual(view['search_results']['A']['candidates']['bangumi'], [])

    def test_invalid_tvdb_detail_is_rejected_before_catalog_fetch(self):
        with patch('backend.clients.tvdb.get_series', AsyncMock(return_value=Mock(json=lambda: {'data': {'id': 100}}))), \
             patch('backend.services.tvdb.fetch_tvdb_series_episodes', AsyncMock()) as catalog:
            with self.assertRaises(HTTPException):
                asyncio.run(service.augment_preview_session(self.row.id, 1, 'A', 'tvdb', 99))
        catalog.assert_not_awaited()
        self.assertEqual(service.load_preview_session(self.row.id)[0].revision, 1)

    def test_manual_special_mapping_uses_one_scoped_candidate_set(self):
        result = copy.deepcopy(self.result)
        result['specials'] = [{'file_name': 'extra.mkv', 'torrent_path': 'SPs/extra.mkv', 'show_name': None}]
        self.row = service.create_preview_session(result, str(self.source))
        body = self.request()
        body['files'][0]['file_id'] = service.file_id('SPs/extra.mkv')
        self.assertEqual(self.restore(body)['files'][0]['resource_identity']['tmdb_series_id'], 1)
        body['files'][0]['mapping']['tmdb'].update(series_id=3, episode_id=31, season_number=1, episode_number=1)
        self.assert_rejected(body)

    def test_names_come_from_final_candidates_and_distinct_works_keep_own_roots(self):
        from backend.services.torrent.preview import derive_series_name
        from backend.services.torrent.monitor import _make_sub_for_path
        files = self.restore()['files']
        files[0]['tmdb_show_name'] = 'Final chosen title'
        self.assertEqual(derive_series_name(self.snapshot, files), 'Final chosen title')
        second = copy.deepcopy(files[0])
        second['resource_identity']['tmdb_series_id'] = 3
        second['tmdb_show_name'] = 'Other chosen title'
        self.assertEqual(derive_series_name(self.snapshot, files + [second]), '')
        self.assertEqual(_make_sub_for_path(second, '')['series_name'], 'Other chosen title')
