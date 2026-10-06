"""Single episode commits share mapping, select video files, and reject failures."""
import io
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException, UploadFile
from backend.services import downloader
from backend.api import routes_history


class EpisodeSubmissionTests(unittest.IsolatedAsyncioTestCase):
    def mocks(self, stack, *, success=True, files=None):
        values = {
            'get_tmdb_id': 0, 'get_tvdb_id': 0,
            'get_all_episodes': {'3': {'tmdb_ep': 17, 'tmdb_season': 0}},
            'compute_info_hash': 'new-hash', 'qb_login': object(),
            'add_torrent': 'new-hash',
            'get_torrent_files': files if files is not None else [
                {'name': 'readme.txt'}, {'name': 'original.mkv'}, {'name': 'subtitle.ass'}],
            'generate_metadata': success, 'delete_torrent': None,
            'resume_torrent': True, 'mark_downloaded': None, 'reset_fail_count': None,
        }
        asynchronous = {'qb_login', 'add_torrent', 'get_torrent_files', 'generate_metadata', 'delete_torrent', 'resume_torrent'}
        return {name: stack.enter_context(patch.object(downloader, name,
                    **({'new': AsyncMock(return_value=value)} if name in asynchronous else {'return_value': value})))
                for name, value in values.items()}

    async def test_all_sources_use_video_and_complete_mapping(self):
        sub = {'name': 'Show', 'bgm': {'season': 2},
               'tmdb': {'id': 10, 'season': 0, 'ep_offset': 4},
               'tvdb': {'id': 20, 'season': 2, 'ep_offset': 8}}
        for source in ('primary', 'backup', 'add', 'edit'):
            with self.subTest(source=source), ExitStack() as stack:
                m = self.mocks(stack)
                await downloader.submit_episode_torrent('x.torrent', 12, 3, source, sub=sub, guid='title')
                args = m['generate_metadata'].await_args
                self.assertEqual(args.args[7], 'original.mkv')
                self.assertEqual(args.kwargs['tmdb_ep_offset'], 4)
                self.assertEqual(args.kwargs['tvdb_id'], 20)
                self.assertEqual(args.kwargs['tvdb_ep'], 11)
                self.assertEqual(args.kwargs['tmdb_season'], 0)
                self.assertEqual(m['mark_downloaded'].call_args.kwargs['tmdb_ep_calc'], 17)
                self.assertEqual(m['mark_downloaded'].call_args.args[4], source)

    async def test_tvdb_only_subscription_generates_metadata(self):
        with ExitStack() as stack:
            m = self.mocks(stack)
            await downloader.submit_episode_torrent('x', 12, 3, 'add', sub={'tvdb': {'id': 20}}, guid='title')
            self.assertEqual(m['generate_metadata'].await_args.args[5], 0)
            self.assertEqual(m['generate_metadata'].await_args.kwargs['tvdb_id'], 20)

    async def test_failure_removes_torrent_without_success_history(self):
        for files, success in ((None, False), ([{'name': 'subtitle.ass'}], True)):
            with ExitStack() as stack:
                m = self.mocks(stack, files=files, success=success)
                with self.assertRaises(RuntimeError):
                    await downloader.submit_episode_torrent('x', 12, 3, 'add', sub={'tmdb': {'id': 10}}, guid='title')
                m['delete_torrent'].assert_awaited_once_with(m['qb_login'].return_value, 'new-hash', delete_files=False)
                m['resume_torrent'].assert_not_awaited()
                m['mark_downloaded'].assert_not_called()

    async def test_missing_metadata_does_not_delete_old_torrent(self):
        with ExitStack() as stack:
            m = self.mocks(stack)
            with self.assertRaises(HTTPException):
                await downloader.submit_episode_torrent('x', 12, 3, 'edit', sub={}, guid='title', replace_existing=True)
            m['delete_torrent'].assert_not_awaited()
            m['add_torrent'].assert_not_awaited()

    async def test_invalid_replace_upload_never_submits(self):
        file = UploadFile(filename='broken.torrent', file=io.BytesIO(b'invalid'))
        with patch.object(downloader, 'resolve_episode_paths', return_value=({}, '', '')), \
             patch.object(downloader, 'submit_episode_torrent', AsyncMock()) as submit:
            with self.assertRaises(HTTPException) as raised:
                await routes_history.replace_episode_torrent(12, 3, file)
            self.assertEqual(raised.exception.status_code, 400)
            submit.assert_not_awaited()
