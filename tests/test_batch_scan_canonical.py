from tests.legacy_helpers import canonical_batch_fixture
"""Phase 4 boundaries use explicit identity without metadata/name inference."""
import copy
import unittest
from unittest.mock import AsyncMock, patch
from backend.services.batch_episode_mapper import normalize_batch_episode
from backend.services.scan_episode_matcher import resolve_scanned_episode
from backend.domain.episode_metadata_adapters import provider_metadata_candidates
from tests.legacy_helpers import mapping_to_legacy_batch_episode
from backend.services.episode_metadata_resolver import resolve_episode


class BatchScanCanonicalTests(unittest.TestCase):
    def test_provider_boundaries_and_projection(self):
        for providers in (("tmdb",), ("tvdb",), ("bangumi",), ("tmdb", "tvdb", "bangumi")):
            with self.subTest(providers=providers):
                raw = {}
                if "tmdb" in providers:
                    raw.update(tmdb_id=10, tmdb_ep_id=11, tmdb_season=0, tmdb_episode=0)
                if "tvdb" in providers:
                    raw.update(tvdb_id=20, tvdb_ep_id=21, tvdb_season=4, tvdb_episode=9)
                if "bangumi" in providers:
                    raw.update(bangumi_subject_id=30, bangumi_ep_id=31,
                               bangumi_episode_number=7, bangumi_episode_sort=0)
                mapping = normalize_batch_episode(canonical_batch_fixture(raw))
                projected = mapping_to_legacy_batch_episode(mapping)
                projected.pop("episode_mapping")
                self.assertEqual(normalize_batch_episode(canonical_batch_fixture(projected)), mapping)
                self.assertEqual(mapping["tmdb"]["season_number"], 0 if "tmdb" in providers else None)
                self.assertEqual(mapping["bangumi"]["episode_absolute"], 0 if "bangumi" in providers else None)

    def test_missing_episode_id_is_not_guessed(self):
        mapping = normalize_batch_episode(canonical_batch_fixture({"tmdb_id": 10, "tmdb_season": 0, "tmdb_episode": 0}))
        before = copy.deepcopy(mapping)
        resolved = resolve_episode(mapping, provider_metadata_candidates(tmdb={"id": 999, "vote_average": 0}))
        self.assertEqual(mapping, before)
        self.assertIsNone(resolved["mapping"]["tmdb"]["episode_id"])
        self.assertEqual(resolved["metadata"]["rating"], 0)

    def test_multi_series_explicit_ids_win_over_titles(self):
        for sid in (10, 20):
            mapping = normalize_batch_episode(canonical_batch_fixture({"tmdb_id": sid, "tmdb_season": 1, "tmdb_episode": 2, "title": "same"}))
            observation = {"torrent_path": "a.torrent", "file_path": "Season 0/a.mkv", "file_name": "a.mkv",
                           "parsed_season_number": 0, "parsed_episode_number": 0}
            resolved = resolve_scanned_episode(observation, {"episode_mapping": mapping, "tmdb_id": 999})
            self.assertEqual(resolved["tmdb"]["series_id"], sid)
            self.assertEqual(resolved["parsed"], {"season_number": 0, "episode_number": 0})
            self.assertEqual(mapping["parsed"]["season_number"], None)

    def test_observation_alone_is_unresolved(self):
        with self.assertRaisesRegex(ValueError, "unresolved_episode"):
            normalize_batch_episode(canonical_batch_fixture({"title": "known", "episode": 1}))


class ScanLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_process_torrent_success_and_failure(self):
        from backend.services.torrent import batch_service
        for error in ("", "failed"):
            preview = {"episodes": {"a.mkv": {"oldPath": "a.mkv", "tmdb_id": 10,
                       "tmdb_season": 0, "tmdb_episode": 0}}}
            preview["episodes"]["a.mkv"] = canonical_batch_fixture(preview["episodes"]["a.mkv"])
            with patch.object(batch_service, "build_preview", AsyncMock(return_value=preview)), \
                 patch.object(batch_service, "execute_confirm", AsyncMock(return_value={"error": error})) as confirm:
                self.assertEqual(await batch_service.process_torrent("a.torrent"), not error)
                mapping = confirm.call_args.args[0]["episodes"]["a.mkv"]["episode_mapping"]
                self.assertEqual(mapping["parsed"]["episode_number"], 0)

    async def test_collection_preserves_provider_and_output_coordinates(self):
        import tempfile
        from pathlib import Path
        import xml.etree.ElementTree as ET
        from backend.services.torrent import batch_service
        from backend.services.nfo.metadata_context import MetadataContext
        from tests.legacy_helpers import seed_preview_metadata
        mapping = normalize_batch_episode(canonical_batch_fixture({"tmdb_id": 10, "tmdb_ep_id": 11,
                    "tmdb_season": 4, "tmdb_episode": 9,
                    "bangumi_subject_id": 20, "bangumi_ep_id": 21,
                    "bangumi_episode_number": 7, "bangumi_episode_sort": 0}))
        ctx = MetadataContext()
        catalog = {"4": {"episodes": [{"tmdbId": 11, "epNum": 9}]}}
        seed_preview_metadata(ctx, {"episode_data": {"tmdb": {"10": catalog},
                            "bangumi": {"20": {"episodes": [{"id": 21}]}}}})
        with patch('backend.services.tmdb.build_season_episode_map', AsyncMock()) as fetch:
            self.assertIs(await ctx.get_tmdb_season_map(10, 'zh-CN'), catalog)
            fetch.assert_not_awaited()
        show = {"title": "番剧", "original_title": "原名", "plot": "中文简介", "tmdb_series_id": 10}
        episode = {"episode_mapping": mapping, "season_number": 0, "episode_number": 0,
                   "bangumi_subject_name": "番剧", "metadata_candidates": provider_metadata_candidates(tmdb={"id": 11, "name": "标题", "overview": "中文简介", "vote_average": 0})}
        before = copy.deepcopy(mapping)
        with tempfile.TemporaryDirectory() as root, \
             patch('backend.clients.tmdb.get_tv_detail', AsyncMock(return_value={})), \
             patch.object(batch_service, 'download_show_images', AsyncMock(return_value={})), \
             patch('backend.services.nfo.plot_fallback.resolve_episode_plot', AsyncMock()) as plot:
            summary = await batch_service.generate_metadata_collection(show, {}, {'a.mkv': episode}, root, ctx)
            xml = ET.parse(Path(root) / 'Season 0' / '番剧 00.nfo').getroot()
            self.assertEqual(xml.findtext('season'), '0')
            self.assertEqual(xml.findtext('episode'), '0')
            self.assertEqual(xml.findtext('bangumiid'), '21')
            self.assertEqual(summary['nfoGenerated'], 2)
            plot.assert_not_awaited()
        self.assertEqual(mapping, before)

    async def test_scan_worker_deletes_only_successful_torrents(self):
        import tempfile
        from pathlib import Path
        from backend.api import routes_system, state
        with tempfile.TemporaryDirectory() as root:
            success = Path(root) / 'a.torrent'
            failed = Path(root) / 'b.torrent'
            success.write_bytes(b'a')
            failed.write_bytes(b'b')
            with patch.object(routes_system, 'process_torrent', AsyncMock(side_effect=[True, False])) as process:
                await routes_system._scan_worker_with_context(root)
            self.assertEqual(process.await_args_list[0].args, (str(success),))
            self.assertFalse(success.exists())
            self.assertTrue(failed.exists())
            self.assertEqual(state._scan_status['processed'], 2)
            self.assertEqual(state._scan_status['failed'], 1)
            self.assertFalse(state._scan_status['running'])
