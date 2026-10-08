from tests.legacy_helpers import canonical_batch_fixture
"""Exercise actual provider boundaries offline, without mutating user data."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from backend.services.torrent import preview, batch_service


class ResourceRecognitionFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_known_tmdb_link_skips_bangumi_title_search(self):
        linked = [{'bangumi_id': 5, 'name': 'A', 'name_original': 'Original', 'tmdb_season': 1}]
        with patch.object(preview.data_store, 'get_map_entries_by_tmdb_id', return_value=linked), \
             patch.object(preview.bangumi_service, 'search_bangumi', AsyncMock()) as search:
            result = await preview._search_bangumi_for_name('A', tmdb_id=10)
        self.assertEqual(result['first']['id'], 5)
        search.assert_not_awaited()

    async def test_preview_multiple_exact_titles_require_confirmation(self):
        response = SimpleNamespace(json=lambda: {'results': [
            {'id': 1, 'name': 'A'}, {'id': 2, 'name': 'A'}]})
        with patch.object(preview.tmdb_client, 'search_tv', AsyncMock(return_value=response)):
            result = await preview._search_tmdb_for_name('A')
        self.assertIsNone(result["first"])
        self.assertEqual(result["resolution"]["status"], "ambiguous")
        self.assertIsNone(result["resolution"]["identity"])
        self.assertEqual(len(result["resolution"]["candidates"]), 2)

    async def test_preview_year_selects_resource_without_first_result(self):
        response = SimpleNamespace(json=lambda: {'results': [
            {'id': 1, 'name': 'A', 'first_air_date': '2000-01-01'},
            {'id': 2, 'name': 'A', 'first_air_date': '2001-01-01'}]})
        with patch.object(preview.tmdb_client, 'search_tv', AsyncMock(return_value=response)):
            result = await preview._search_tmdb_for_name('A 2001')
        self.assertEqual(result['first']['id'], 2)
        self.assertEqual(result['rest'][0]['id'], 1)

    async def test_batch_mixed_series_rejected_before_any_provider_request(self):
        episodes = [{'showName': 'A', 'season': 1, 'episode': 1},
                    {'showName': 'B', 'season': 1, 'episode': 1}]
        with patch.object(batch_service, 'read_torrent_file_list', return_value=[]), \
             patch.object(batch_service, 'parse_qbit_file_list', return_value={'episodes': episodes, 'extras': []}), \
             patch.object(batch_service.tmdb_service, 'search_tv_show', AsyncMock()) as search:
            with self.assertRaisesRegex(ValueError, 'batch_multiple_series'):
                await batch_service.build_preview('/tmp/input.torrent')
        search.assert_not_awaited()

    def test_chain_title_selection_independent_of_order(self):
        rows = [{'id': 1, 'name': 'A'}, {'id': 2, 'name': 'B'}]
        for ordered in (rows, rows[::-1]):
            self.assertEqual(batch_service._find_entry_in_chain('B', ordered), 2)
        with self.assertRaisesRegex(ValueError, 'ambiguous_resource'):
            batch_service._find_entry_in_chain('Missing', rows)


class PreviewAmbiguityTests(unittest.IsolatedAsyncioTestCase):
    async def test_mapping_deduplicates_without_confirming_seasons(self):
        linked = [{'bangumi_id': 1, 'name': 'Season 1'},
                  {'bangumi_id': 2, 'name': 'Season 2'},
                  {'bangumi_id': 1, 'name': 'Season 1'}]
        with patch.object(preview.data_store, 'get_map_entries_by_tmdb_id', return_value=linked), \
             patch.object(preview.bangumi_service, 'search_bangumi', AsyncMock()) as search:
            result = await preview._search_bangumi_for_name('Show', 100)
        self.assertIsNone(result['first'])
        self.assertEqual(result['resolution']['status'], 'ambiguous')
        self.assertEqual({c['provider_id'] for c in result['resolution']['candidates']}, {1, 2})
        self.assertEqual(len(result['resolution']['candidates']), 2)
        search.assert_not_awaited()

    async def test_mapping_exact_title_selects_own_subject(self):
        linked = [{'bangumi_id': 1, 'name': 'Season 1'}, {'bangumi_id': 2, 'name': 'Season 2'}]
        with patch.object(preview.data_store, 'get_map_entries_by_tmdb_id', return_value=linked):
            result = await preview._search_bangumi_for_name('Season 2', 100)
        self.assertEqual(result['first']['id'], 2)
        self.assertEqual(result['resolution']['identity']['bangumi_subject_id'], 2)

    async def test_mixed_resolved_ambiguous_unresolved_shows(self):
        response = SimpleNamespace(json=lambda: {'results': [{'id': 100, 'name': 'TV'}]})
        empty = SimpleNamespace(json=lambda: {'results': []})
        linked = [{'bangumi_id': 1, 'name': 'Season 1'}, {'bangumi_id': 2, 'name': 'Season 2'}]
        movie = SimpleNamespace(json=lambda: {'results': [{'id': 200, 'title': 'Movie', 'original_title': 'Movie'}]})
        files = [{'show_name': 'TV'}] * 3 + [{'show_name': 'Movie'}, {'show_name': 'Missing'}]
        with patch.object(preview.tmdb_client, 'search_tv', AsyncMock(return_value=response)), \
             patch.object(preview.tmdb_client, 'search_movie', AsyncMock(side_effect=[movie, empty])), \
             patch.object(preview.data_store, 'get_map_entries_by_tmdb_id', return_value=linked), \
             patch.object(preview.bangumi_service, 'search_bangumi', AsyncMock(return_value=[])):
            pairs = await preview._parallel_search(['TV', 'Movie', 'Missing'], files)
        entries = preview._organize(pairs)['search_results']
        tv = entries['TV']['resource_resolution']
        self.assertEqual(tv['status'], 'ambiguous')
        self.assertEqual(tv['identity']['tmdb_series_id'], 100)
        self.assertIsNone(tv['identity']['bangumi_subject_id'])
        self.assertEqual(entries['Movie']['resource_resolution']['identity']['tmdb_movie_id'], 200)
        self.assertEqual(entries['Missing']['resource_resolution']['status'], 'unresolved')

    async def test_provider_errors_are_not_uncertainty(self):
        import asyncio
        for error in (TimeoutError('network'), ValueError('malformed_response'), asyncio.CancelledError()):
            with patch.object(preview.tmdb_client, 'search_movie', AsyncMock(side_effect=error)):
                with self.assertRaises(type(error) if isinstance(error, (ValueError, asyncio.CancelledError)) else RuntimeError):
                    await preview._parallel_search(['A'], [{'show_name': 'A'}])

    async def test_bangumi_retry_propagates_timeout(self):
        with patch.object(preview.bangumi_service, 'search_bangumi', AsyncMock(side_effect=[[], TimeoutError()])), \
             patch.object(preview.data_store, 'get_map_entries_by_tmdb_id', return_value=[]):
            with self.assertRaises(TimeoutError):
                await preview._search_bangumi_for_name('A', 100, 'Original')

    async def test_full_preview_preserves_mapping_candidates_without_fetching_them(self):
        files = [{'name': f'Show - {ep:02}.mkv'} for ep in range(1, 4)]
        response = SimpleNamespace(json=lambda: {'results': [{'id': 100, 'name': 'Show'}]})
        linked = [{'bangumi_id': 1, 'name': 'Season 1'}, {'bangumi_id': 2, 'name': 'Season 2'}]
        with patch.object(preview, 'read_torrent_file_list', return_value=files), \
             patch('backend.utils.torrent_file_reader.read_torrent_name', return_value='Show'), \
             patch.object(preview.tmdb_client, 'search_tv', AsyncMock(return_value=response)), \
             patch.object(preview.data_store, 'get_map_entries_by_tmdb_id', return_value=linked), \
             patch.object(preview.data_store, 'list_subscriptions', return_value=[]), \
             patch.object(preview.tmdb_service, 'build_season_episode_map', AsyncMock(return_value={})), \
             patch.object(preview.bgm_client, 'get_episodes', AsyncMock()) as episodes:
            result = await preview.parse_and_search('/tmp/input.torrent')
        resolution = result['search_results']['Show']['resource_resolution']
        self.assertEqual(resolution['status'], 'ambiguous')
        self.assertEqual(resolution['identity']['tmdb_series_id'], 100)
        self.assertIsNone(resolution['identity']['bangumi_subject_id'])
        self.assertEqual(len(resolution['candidates']), 3)
        self.assertEqual(result['search_results']['Show']['map_entries'], [])
        episodes.assert_not_awaited()

    async def test_tmdb_first_path_retains_ambiguous_tmdb_candidates(self):
        from backend.services.torrent import search
        response = SimpleNamespace(json=lambda: {'results': [
            {'id': 1, 'name': 'A', 'genre_ids': [16]}, {'id': 2, 'name': 'A', 'genre_ids': [16]}]})
        resolutions = {}
        with patch.object(search.tmdb_client, 'search_tv', AsyncMock(return_value=response)):
            selected = await search._search_tmdb_single('A', resolutions)
        self.assertIsNone(selected)
        self.assertEqual(resolutions['tmdb']['status'], 'ambiguous')
        self.assertEqual(len(resolutions['tmdb']['candidates']), 2)

    async def test_movie_fallback_never_uses_tv_mapping(self):
        linked = [{'bangumi_id': 1, 'name': 'A', 'tmdb_season': 1},
                  {'bangumi_id': 2, 'name': 'Movie', 'tmdb_season': -1}]
        with patch.object(preview.data_store, 'get_map_entries_by_tmdb_id', return_value=linked):
            result = await preview._search_bangumi_for_name('Movie', 100, media_type='movie')
        self.assertEqual(result['first']['id'], 2)
        self.assertEqual(result['resolution']['identity']['media_type'], 'movie')
        self.assertEqual([c['provider_id'] for c in result['resolution']['candidates']], [2])

    async def test_no_valid_link_continues_normal_search(self):
        with patch.object(preview.data_store, 'get_map_entries_by_tmdb_id', return_value=[]), \
             patch.object(preview.bangumi_service, 'search_bangumi', AsyncMock(return_value=[])) as search:
            result = await preview._search_bangumi_for_name('A', 100)
        self.assertEqual(result['resolution']['status'], 'unresolved')
        search.assert_awaited_once_with('A')

    async def test_bangumi_timeout_in_pair_is_provider_failure(self):
        response = SimpleNamespace(json=lambda: {'results': []})
        with patch.object(preview.tmdb_client, 'search_movie', AsyncMock(return_value=response)), \
             patch.object(preview.bangumi_service, 'search_bangumi', AsyncMock(side_effect=TimeoutError())):
            with self.assertRaisesRegex(RuntimeError, 'resource_provider_request_failed'):
                await preview._parallel_search(['A'], [{'show_name': 'A'}])
