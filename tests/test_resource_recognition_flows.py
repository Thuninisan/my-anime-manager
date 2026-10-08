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
            with self.assertRaisesRegex(ValueError, 'ambiguous_resource'):
                await preview._search_tmdb_for_name('A')

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
