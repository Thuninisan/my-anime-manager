"""Unified file inventory across both parsers and the public preview boundary."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from backend.services.torrent import preview, search
from backend.services.torrent.preview_files import unify_files
from backend.services.torrent.preview_session import build_snapshot
from backend.services.torrent.preview_view import build_preview_view


class UnifiedPreviewFilesTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_parsers_preserve_inventory_and_exclusions(self):
        paths = ['Show - 01.mkv', 'Show - 02.mkv', 'Show - 03.mkv',
                 'Show/SPs/Trailer.mkv', 'Show/Show - 01.vtt', 'Show/Fonts.zip',
                 'Show/OST.flac', 'Show/cover.jpg', 'Show/Exclude - 04.mkv',
                 'Show/Exclude.vtt']
        files = [{'name': path} for path in paths]
        for tmdb_first in (False, True):
            with self.subTest(tmdb_first=tmdb_first), \
                 patch.object(preview.config, 'TORRENT_EXCLUDE_PATTERNS', 'Exclude'), \
                 patch.object(preview, 'read_torrent_file_list', return_value=files), \
                 patch.object(search, 'read_torrent_file_list', return_value=files), \
                 patch('backend.utils.torrent_file_reader.read_torrent_name', return_value='Show'), \
                 patch.object(preview, '_parallel_search', AsyncMock(return_value=[])), \
                 patch.object(preview, '_fetch_provider_catalogs', AsyncMock(return_value={'tmdb': {}, 'bangumi': {}, 'tvdb': {}})), \
                 patch.object(search, '_search_tmdb_single', AsyncMock(return_value=None)), \
                 patch('backend.data.get_map_entries_by_tmdb_id', return_value=[]):
                result = (await search.search_by_tmdb('/tmp/input.torrent', torrent_name='Show [ktnbytes]')
                          if tmdb_first else await preview.parse_and_search('/tmp/input.torrent'))
            self.assertEqual([f['torrent_path'] for f in result['parsed_files']], paths)
            inventory = {f['torrent_path']: f for f in result['parsed_files']}
            self.assertEqual(inventory[paths[0]]['processing_status'], 'automatic')
            self.assertEqual(inventory[paths[3]]['category'], 'special')
            self.assertEqual(inventory[paths[3]]['processing_status'], 'manual')
            self.assertEqual(inventory[paths[4]]['type'], 'subtitle')
            self.assertEqual(inventory[paths[4]]['processing_status'], 'associate')
            self.assertEqual(inventory[paths[5]]['type'], 'font')
            self.assertEqual(inventory[paths[6]]['type'], 'audio')
            self.assertEqual(inventory[paths[7]]['type'], 'other')
            for path in paths[8:]:
                self.assertEqual(inventory[path]['processing_status'], 'ignored')
                self.assertEqual(inventory[path]['skip_reason'], 'exclude_pattern')
            for key in ('specials', 'subtitles', 'subtitle_files', 'skipped_files'):
                self.assertNotIn(key, result)
            with tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / 'source.torrent'
                source.write_bytes(b'torrent')
                snapshot = build_snapshot(result, source)
                view = build_preview_view(snapshot, 'preview', 1, 'expiry')
            self.assertEqual(len(view['parsed_files']), len(paths))
            self.assertEqual(len({f['file_id'] for f in view['parsed_files']}), len(paths))
            for key in ('specials', 'subtitles', 'subtitle_files', 'skipped_files'):
                self.assertNotIn(key, view)

    def test_special_and_skip_records_collapse_to_one_file(self):
        file = {'file_name': 'extra.mkv', 'torrent_path': 'SPs/extra.mkv'}
        result = unify_files({'parsed_files': [], 'specials': [file],
                              'skipped_files': [{**file, 'skip_reason': 'skip_directory'}]})
        self.assertEqual(len(result['parsed_files']), 1)
        self.assertEqual(result['parsed_files'][0]['processing_status'], 'manual')
        self.assertIsNone(result['parsed_files'][0]['skip_reason'])
