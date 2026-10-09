"""Search limits, mapping boundaries and concurrent directory loading."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend.services.torrent import preview
from backend.services.torrent.preview_session import candidate_context


def response(key, rows):
    return SimpleNamespace(json=lambda: {key: rows})


class PreviewDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_files_are_movie_and_never_query_mapping_or_tvdb(self):
        rows = [{'id': i, 'name_cn': f'电影{i}'} for i in range(1, 7)]
        with patch.object(preview.tmdb_client, 'search_movie', AsyncMock(return_value=response('results', [
                {'id': 100, 'title': '中文电影', 'original_title': 'Original'}]))) as movie, \
             patch.object(preview.tmdb_client, 'search_tv', AsyncMock()) as tv, \
             patch.object(preview.data_store, 'get_map_entries_by_tmdb_id') as mapping, \
             patch.object(preview.data_store, 'get_map_entry') as reverse, \
             patch.object(preview.tvdb_client, 'search_series', AsyncMock()) as tvdb, \
             patch.object(preview.bangumi_service, 'search_bangumi', AsyncMock(return_value=rows)) as bgm:
            pairs = await preview._parallel_search(['Movie'], [{'show_name': 'Movie'}] * 2)
            entry = preview._organize(pairs)['search_results']['Movie']
            context = candidate_context('Movie', entry, {})
        movie.assert_awaited_once()
        bgm.assert_awaited_once_with('中文电影')
        tv.assert_not_awaited()
        tvdb.assert_not_awaited()
        mapping.assert_not_called()
        reverse.assert_not_called()
        self.assertEqual([c['provider_id'] for c in context['candidates']['bangumi']], [1, 2])
        self.assertEqual(context['candidates']['tvdb'], [])
        self.assertEqual(context['mapping_hints'], [])

    async def test_unmapped_tv_uses_five_bangumi_and_first_tvdb(self):
        with patch.object(preview.tmdb_client, 'search_tv', AsyncMock(return_value=response('results', [
                {'id': 100, 'name': '中文名'}]))), \
             patch.object(preview.data_store, 'get_map_entries_by_tmdb_id', return_value=[]), \
             patch.object(preview.bangumi_service, 'search_bangumi', AsyncMock(return_value=[
                {'id': i, 'name': str(i)} for i in range(1, 9)])) as bgm, \
             patch.object(preview.tvdb_client, 'search_series', AsyncMock(return_value=response('data', [
                {'tvdb_id': '90', 'name': 'First', 'type': 'series'},
                {'tvdb_id': '91', 'name': 'Second', 'type': 'series'}]))) as tvdb:
            pairs = await preview._parallel_search(['Original'], [{'show_name': 'Original'}] * 3)
            entry = preview._organize(pairs)['search_results']['Original']
            context = candidate_context('Original', entry, {})
        bgm.assert_awaited_once_with('中文名')
        tvdb.assert_awaited_once_with('Original')
        self.assertEqual(entry['bangumi_ids'], [1, 2, 3, 4, 5])
        self.assertEqual(entry['tvdb_ids'], [90])
        self.assertEqual([c['provider_id'] for c in context['candidates']['tvdb']], [90])

    async def test_mapping_keeps_all_links_and_skips_search(self):
        links = [{'bangumi_id': i, 'tvdb_id': 90, 'tmdb_season': 1} for i in range(1, 8)]
        links.append({'bangumi_id': 99, 'tvdb_id': 99, 'tmdb_season': -1})
        with patch.object(preview.tmdb_client, 'search_tv', AsyncMock(return_value=response('results', [{'id': 100, 'name': 'TV'}]))), \
             patch.object(preview.data_store, 'get_map_entries_by_tmdb_id', return_value=links), \
             patch.object(preview.bangumi_service, 'search_bangumi', AsyncMock()) as bgm, \
             patch.object(preview.tvdb_client, 'search_series', AsyncMock()) as tvdb:
            pairs = await preview._parallel_search(['TV'], [{'show_name': 'TV'}] * 3)
            entry = preview._organize(pairs)['search_results']['TV']
        self.assertEqual(entry['bangumi_ids'], list(range(1, 8)))
        self.assertEqual(entry['tvdb_ids'], [90])
        bgm.assert_not_awaited()
        tvdb.assert_not_awaited()

    async def test_partial_mapping_searches_only_missing_source(self):
        for links, expected in [([{'bangumi_id': 1}], 'tvdb'), ([{'tvdb_id': 90}], 'bangumi')]:
            with self.subTest(expected=expected), \
                 patch.object(preview.tmdb_client, 'search_tv', AsyncMock(return_value=response('results', [{'id': 100, 'name': 'TV'}]))), \
                 patch.object(preview.data_store, 'get_map_entries_by_tmdb_id', return_value=links), \
                 patch.object(preview.bangumi_service, 'search_bangumi', AsyncMock(return_value=[])) as bgm, \
                 patch.object(preview.tvdb_client, 'search_series', AsyncMock(return_value=response('data', []))) as tvdb:
                await preview._parallel_search(['TV'], [{'show_name': 'TV'}] * 3)
                self.assertEqual(bgm.await_count, int(expected == 'bangumi'))
                self.assertEqual(tvdb.await_count, int(expected == 'tvdb'))

    async def test_catalogs_overlap_and_deduplicate_ids_without_relations(self):
        started = set()
        ready = asyncio.Event()

        async def enter(provider):
            started.add(provider)
            if len(started) == 3:
                ready.set()
            await asyncio.wait_for(ready.wait(), 1)

        async def tmdb(*args, **kwargs):
            await enter('tmdb')
            return {}

        async def subject(*args):
            await enter('bangumi')
            return {'name': 'B'}

        async def tvdb(*args):
            await enter('tvdb')
            return {'seasons': {}}

        entry = {'media_type': 'tv', 'tmdb': {'id': 100}, 'bangumi_ids': [1, 2],
                 'tvdb_ids': [90], 'discovery_complete': True}
        with patch.object(preview.tmdb_service, 'build_season_episode_map', AsyncMock(side_effect=tmdb)) as tm, \
             patch.object(preview.bgm_client, 'get_subject', AsyncMock(side_effect=subject)) as subjects, \
             patch.object(preview.bgm_client, 'get_episodes', AsyncMock(return_value=[])), \
             patch.object(preview.bgm_client, 'get_relations', AsyncMock()) as relations, \
             patch('backend.services.tvdb.fetch_tvdb_series_episodes', AsyncMock(side_effect=tvdb)) as tv:
            result = await preview._fetch_provider_catalogs({'A': entry, 'B': dict(entry)}, [])
        self.assertEqual(started, {'tmdb', 'bangumi', 'tvdb'})
        self.assertEqual(tm.await_count, 1)
        self.assertEqual(tv.await_count, 1)
        self.assertEqual(subjects.await_count, 2)
        self.assertEqual(set(result['bangumi']), {'1', '2'})
        relations.assert_not_awaited()

    async def test_movie_catalogs_ignore_mapping_and_seasons(self):
        with patch.object(preview.data_store, 'get_map_entries_by_tmdb_id') as mapping, \
             patch.object(preview.tmdb_service, 'build_season_episode_map', AsyncMock()) as tmdb, \
             patch.object(preview.bgm_client, 'get_subject', AsyncMock(return_value={'name': 'Movie'})), \
             patch.object(preview.bgm_client, 'get_episodes', AsyncMock(return_value=[])), \
             patch('backend.services.tvdb.fetch_tvdb_series_episodes', AsyncMock()) as tvdb:
            result = await preview._fetch_provider_catalogs({'Movie': {
                'media_type': 'movie', 'tmdb': {'id': 100}, 'bangumi_ids': [1, 2],
                'tvdb_ids': [90], 'map_entries': [{'bangumi_id': 3, 'tvdb_id': 91}]}}, [])
        self.assertEqual(set(result['bangumi']), {'1', '2'})
        self.assertEqual(result['tmdb'], {})
        self.assertEqual(result['tvdb'], {})
        mapping.assert_not_called()
        tmdb.assert_not_awaited()
        tvdb.assert_not_awaited()

    async def test_tmdb_searches_really_overlap(self):
        ready = asyncio.Event()
        calls = []

        async def search(name, **kwargs):
            calls.append(name)
            if len(calls) == 2:
                ready.set()
            await asyncio.wait_for(ready.wait(), 1)
            return response('results', [])

        with patch.object(preview.tmdb_client, 'search_movie', AsyncMock(side_effect=search)), \
             patch.object(preview.bangumi_service, 'search_bangumi', AsyncMock(return_value=[])):
            await preview._parallel_search(['A', 'B'], [{'show_name': 'A'}, {'show_name': 'B'}])
        self.assertEqual(set(calls), {'A', 'B'})

    async def test_bangumi_gate_spaces_starts_instead_of_whole_responses(self):
        client = preview.bgm_client
        with patch.object(client, '_request_gate', asyncio.Lock()), \
             patch.object(client, '_last_request_started', 10.0), \
             patch.object(client.config, 'API_DELAY_MS', 600), \
             patch.object(client.time, 'monotonic', side_effect=[10.2, 10.6, 11.4, 11.4]), \
             patch.object(client.asyncio, 'sleep', AsyncMock()) as sleep:
            await client._delay()
            await client._delay()
        self.assertEqual(sleep.await_count, 1)
        self.assertAlmostEqual(sleep.await_args.args[0], 0.4)

    async def test_tvdb_concurrent_authentication_logs_in_once(self):
        client = preview.tvdb_client
        async def login():
            await asyncio.sleep(0)
            client._token = 'token'
            return 'token'
        with patch.object(client, '_token', None), \
             patch.object(client, '_login_lock', asyncio.Lock()), \
             patch.object(client.config, 'TVDB_API_KEY', 'test'), \
             patch.object(client, 'login', AsyncMock(side_effect=login)) as auth:
            tokens = await asyncio.gather(client._ensure_auth(), client._ensure_auth())
        self.assertEqual(tokens, ['token', 'token'])
        auth.assert_awaited_once()

    async def test_failed_request_cancels_and_awaits_siblings(self):
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def waiting():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def failing():
            await started.wait()
            raise RuntimeError('provider failed')

        with self.assertRaisesRegex(RuntimeError, 'provider failed'):
            await preview._gather_provider_requests(waiting(), failing())
        self.assertTrue(cancelled.is_set())
