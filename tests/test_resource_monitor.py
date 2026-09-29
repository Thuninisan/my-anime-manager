"""Small regression checks for resource feed normalization and storage."""

import tempfile
import unittest
import asyncio
import io
import sys
from contextlib import redirect_stdout
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from backend.db import resources, connection, resource_recognitions
from backend.services.resource_monitor.details import extract_description
from backend.services.resource_monitor.feeds import parse_feed
from backend.services.resource_monitor.titles import parse_title
from backend.services.resource_monitor.recognize import compare_episode_names, recognize_resource
from backend.api.routes_resources import collect_now
from backend.api.routes_resources import router as resource_router
from backend.services.resource_monitor import worker
from fastapi import FastAPI
from fastapi.testclient import TestClient


class ResourceFeedTests(unittest.TestCase):
    def test_debug_save_writes_completed_result_only(self):
        from scripts.debug import rss_title
        resource = {"id": 23, "source": "ktnbytes", "title": "Oreimo S02 01-13", "index_type": "tmdb"}
        complete = {"status": "complete", "title_snapshot": {"rule_version": 3},
                    "candidates": [{"bangumi_id": 37898}]}
        with (patch.object(rss_title, "recognize_resource", new_callable=AsyncMock,
                           return_value=complete),
              patch.object(rss_title.resource_recognitions, "save") as save,
              patch.object(rss_title.resource_recognitions, "get",
                           return_value={"status": "complete", "candidates": complete["candidates"]}),
              redirect_stdout(io.StringIO())):
            status = rss_title.run_resource(resource, save=True)
        self.assertEqual(status, "complete")
        save.assert_called_once()
        with (patch.object(rss_title, "recognize_resource", new_callable=AsyncMock,
                           return_value={"status": "failed", "error": "offline"}),
              patch.object(rss_title.resource_recognitions, "save") as save,
              redirect_stdout(io.StringIO())):
            status = rss_title.run_resource(resource, save=True)
        self.assertEqual(status, "failed")
        save.assert_not_called()
        unresolved = {"status": "unresolved", "reason": "映射表无条目",
                      "title_snapshot": {"rule_version": 3}, "candidates": []}
        with (patch.object(rss_title, "recognize_resource", new_callable=AsyncMock,
                           return_value=unresolved),
              patch.object(rss_title.resource_recognitions, "save") as save,
              patch.object(rss_title.resource_recognitions, "get",
                           return_value={"status": "unresolved", "candidates": []}),
              redirect_stdout(io.StringIO())):
            status = rss_title.run_resource(resource, save=True)
        self.assertEqual(status, "unresolved")
        self.assertEqual(save.call_args.args[2], "unresolved")

    def test_debug_script_runs_five_selected_resources_by_default(self):
        from scripts.debug import rss_title
        selected = []

        def pick(_database, resource_id, _source):
            selected.append(resource_id)
            return {"id": resource_id}

        with (patch.object(rss_title, "pick_resource", side_effect=pick),
              patch.object(rss_title, "run_resource", return_value="complete"),
              patch.object(sys, "argv", ["rss_title.py"]),
              redirect_stdout(io.StringIO())):
            exit_code = rss_title.main()
        self.assertEqual(exit_code, 0)
        self.assertEqual(selected, [23, 52, 51, 76, 63])

    def test_app_startup_starts_resource_worker(self):
        from backend.api.app import app
        with patch.object(worker, "start") as start:
            with TestClient(app):
                start.assert_called_once_with()

    def test_resource_poll_recognizes_only_unprocessed_records(self):
        record = {"id": 7, "title": "Example", "index_type": "tmdb"}
        with (patch.object(worker, "list_sources", return_value={"example": {}}),
              patch.object(worker, "collect_source", new_callable=AsyncMock,
                           return_value={"source": "example", "seen": 1, "complete": 1, "failed": 0}),
              patch.object(worker.resource_recognitions, "list_unrecognized_resources", return_value=[record]) as pending,
              patch.object(worker, "recognize_resource", new_callable=AsyncMock,
                           return_value={"status": "complete"}) as recognize):
            result = asyncio.run(worker.run_once())
        pending.assert_called_once_with()
        recognize.assert_awaited_once_with(record)
        self.assertEqual(result["recognized"], 1)

    def test_title_season_range_and_movie(self):
        parsed = parse_title('[VCB-Studio] 咒术回战 / Jujutsu Kaisen 1080p [S1+S2+MOVIE]', 'tvdb')
        self.assertEqual(parsed['seasons'], [1, 2])
        self.assertEqual(parsed['media_types'], ['TV', 'MOVIE'])
        self.assertIn('Jujutsu Kaisen', parsed['name_candidates'])
        ranged = parse_title('[VCB-Studio] Komi-san 1080p [S1-2 Fin]', 'tvdb')
        self.assertEqual(ranged['seasons'], [1, 2])
        self.assertIsNone(ranged['episode_range'])
        self.assertEqual(parse_title('[VCB-Studio] SANDA 1080p [Fin]', 'tvdb')['seasons'], [1])
        mixed = parse_title('[VCB-Studio] Minami-ke [S2-S4 + OADs + S1 Fin]', 'tvdb')
        self.assertEqual(mixed['seasons'], [1, 2, 3, 4])
        movie = parse_title('[VCB-Studio] Detective Conan [MOVIE Fin]', 'tvdb')
        self.assertEqual(movie['seasons'], [])
        self.assertEqual(movie['media_types'], ['MOVIE'])
        special = parse_title('Oreimo S02 01-13+SPx3 BDrip', 'tmdb')
        self.assertEqual(special['episode_range'], [1, 13])
        self.assertEqual(special['special_count'], 3)

    def test_tmdb_explicit_season_mapping_excludes_null_season_sibling(self):
        record = {'id': 23, 'title': '我的妹妹哪有這麼可愛。/Ore no Imouto S02 01-13+SPx3',
                  'index_type': 'tmdb'}
        response = SimpleNamespace(json=lambda: {'results': [{'id': 56353, 'name': 'Oreimo'}]})
        season = SimpleNamespace(json=lambda: {'episodes': [{'episode_number': 1, 'name': 'First'}]})
        with (patch('backend.services.resource_monitor.recognize.config.TMDB_API_KEY', 'test-key'),
              patch('backend.services.resource_monitor.recognize.tmdb.search_tv',
                    new_callable=AsyncMock, return_value=response),
              patch('backend.services.resource_monitor.recognize.tmdb.get_season_detail',
                    new_callable=AsyncMock, return_value=season),
              patch('backend.services.resource_monitor.recognize.bangumi.get_episodes',
                    new_callable=AsyncMock, return_value=[{'sort': 1, 'name': 'First'}])):
            result = asyncio.run(recognize_resource(record, persist=False))
        self.assertEqual([item['bangumi_id'] for item in result['candidates']], [37898])

    def test_pure_movie_uses_movie_title_mapping_without_tv_season(self):
        record = {'id': 63, 'title': '[VCB-Studio] 名侦探柯南M21 唐红的恋歌 / '
                  '名探偵コナン から紅の恋歌 [MOVIE Fin]', 'index_type': 'tvdb'}
        response = SimpleNamespace(json=lambda: {'data': []})
        with (patch('backend.services.resource_monitor.recognize.config.TVDB_API_KEY', 'test-key'),
              patch('backend.services.resource_monitor.recognize.tvdb.search_movie',
                    new_callable=AsyncMock, return_value=response),
              patch('backend.services.resource_monitor.recognize.tvdb.search_series',
                    new_callable=AsyncMock) as series):
            result = asyncio.run(recognize_resource(record, persist=False))
        self.assertEqual(result['status'], 'complete')
        self.assertEqual([(item['bangumi_id'], item['media_type']) for item in result['candidates']],
                         [(198962, 'MOVIE')])
        series.assert_not_called()

    def test_episode_match_count_cannot_exceed_index_episode_count(self):
        index = [{'epNum': 1, 'name': 'Same'}]
        bangumi = [{'sort': number, 'name': 'Same'} for number in range(1, 4)]
        self.assertEqual(compare_episode_names(index, bangumi, 'epNum')[1], 1)

    def test_real_vcb_multi_season_movie_candidates(self):
        record = {'id': 51, 'title': '[豌豆字幕组&风之圣殿&VCB-Studio] 咒术回战 / Jujutsu Kaisen / 呪术廻戦 10-bit 1080p HEVC BDRip [S1+S2+MOVIE Reseed V2 Fin]', 'index_type': 'tvdb'}
        cache = {('tvdb', 'search', '咒术回战'): [{'tvdb_id': 377543, 'name': 'Jujutsu Kaisen'}],
                 ('tvdb', 'episodes', 377543, 1): None,
                 ('tvdb', 'episodes', 377543, 2): None}
        from backend import data as data_store
        for entry in data_store.get_map_entries_by_tvdb_id(377543):
            cache[('bangumi', entry['bangumi_id'])] = None
        with (patch('backend.services.resource_monitor.recognize.resource_recognitions.save'),
              patch('backend.services.resource_monitor.recognize.config.TVDB_API_KEY', 'test-key')):
            result = asyncio.run(recognize_resource(record, cache))
        self.assertEqual(result['status'], 'complete')
        self.assertIn((294993, 'TV'), {(c['bangumi_id'], c['media_type']) for c in result['candidates']})
        self.assertIn((369304, 'TV'), {(c['bangumi_id'], c['media_type']) for c in result['candidates']})
        self.assertIn((331559, 'MOVIE'), {(c['bangumi_id'], c['media_type']) for c in result['candidates']})

    def test_missing_tvdb_key_waits_without_request_or_traceback(self):
        record = {'id': 999, 'title': '[VCB-Studio] SANDA 1080p [Fin]', 'index_type': 'tvdb'}
        with (patch('backend.services.resource_monitor.recognize.config.TVDB_API_KEY', ''),
              patch('backend.services.resource_monitor.recognize.tvdb.search_series', new_callable=AsyncMock) as search,
              patch('backend.services.resource_monitor.recognize.resource_recognitions.save') as save):
            result = asyncio.run(recognize_resource(record))
        self.assertEqual(result['status'], 'waiting_config')
        self.assertIn('TVDB_API_KEY', result['error'])
        search.assert_not_called()
        self.assertEqual(save.call_args.args[2], 'waiting_config')

    def test_read_only_network_recognition_reports_steps(self):
        record = {'id': 51, 'title': '[VCB-Studio] 咒术回战 / Jujutsu Kaisen [S1+S2+MOVIE]',
                  'index_type': 'tvdb'}
        response = SimpleNamespace(json=lambda: {'data': [{'tvdb_id': 377543, 'name': 'Jujutsu Kaisen'}]})
        series = {'seasons': {1: {'episodes': [{'epNum': 1, 'name': 'First Day'}]},
                              2: {'episodes': [{'epNum': 1, 'name': 'First Day'}]}}}
        steps = []
        with (patch('backend.services.resource_monitor.recognize.config.TVDB_API_KEY', 'test-key'),
              patch('backend.services.resource_monitor.recognize.tvdb.search_series',
                    new_callable=AsyncMock, return_value=response) as search,
              patch('backend.services.resource_monitor.recognize.fetch_tvdb_series_episodes',
                    new_callable=AsyncMock, return_value=series) as episodes,
              patch('backend.services.resource_monitor.recognize.bangumi.get_episodes',
                    new_callable=AsyncMock, return_value=[{'sort': 1, 'name': 'First Day'}]),
              patch('backend.services.resource_monitor.recognize.resource_recognitions.save') as save):
            result = asyncio.run(recognize_resource(
                record, persist=False, on_step=lambda name, data: steps.append((name, data))))
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(search.await_count, 1)
        self.assertEqual(episodes.await_count, 1)
        self.assertIn('mapping', [name for name, _ in steps])
        self.assertIn('bangumi_comparison', [name for name, _ in steps])
        save.assert_not_called()

    def test_episode_comparison_retains_excludes_and_preserves_unknown(self):
        index = [{'epNum': 1, 'name': 'First Day'}]
        self.assertEqual(compare_episode_names(index, [{'sort': 1, 'name': 'First Day'}], 'epNum')[0], 'retained')
        self.assertEqual(compare_episode_names(index, [{'sort': 1, 'name': 'Unrelated'}], 'epNum')[0], 'excluded')
        self.assertEqual(compare_episode_names(index, [{'sort': 1, 'name': ''}], 'epNum')[0], 'uncomparable')
        self.assertEqual(compare_episode_names(None, [{'sort': 1, 'name': 'First Day'}], 'epNum')[0], 'uncomparable')

    def test_configured_download_tag_errors(self):
        xml = b'<rss><channel><item><title>Show</title><guid>https://example.com/post</guid><link>https://example.com/post</link></item></channel></rss>'
        config = {'downloadtag': {'tag': 'link', 'attribute': None}, 'index_type': 'tmdb'}
        with self.assertRaisesRegex(ValueError, '详情页'):
            parse_feed('custom', xml, config)
        config['downloadtag'] = {'tag': 'enclosure', 'attribute': 'url'}
        with self.assertRaisesRegex(ValueError, '缺少'):
            parse_feed('custom', xml, config)

    def test_collect_now_has_running_event_loop(self):
        async def run():
            with patch.object(worker, "run_once", new_callable=AsyncMock) as mocked:
                result = await collect_now()
                self.assertTrue(result["polling"])
                await asyncio.sleep(0)
                self.assertTrue(mocked.called)
                if worker._manual_task:
                    await worker._manual_task
        asyncio.run(run())

    def test_run_once_http_returns_and_starts_background_task(self):
        app = FastAPI()
        app.include_router(resource_router)
        with patch.object(worker, 'run_once', new_callable=AsyncMock) as mocked:
            with TestClient(app) as client:
                response = client.post('/api/resources/run-once')
            self.assertEqual(response.status_code, 200)
            self.assertTrue(mocked.called)

    def test_ktnbytes_enclosure_and_detail_url(self):
        xml = b"""<rss><channel><item><title>Show S01 01-12</title>
            <guid>https://ktnbytes.com/post</guid><link>https://ktnbytes.com/post</link>
            <description><![CDATA[ ]]></description>
            <enclosure url="https://ktnbytes.com/post/file.torrent" />
            </item></channel></rss>"""
        item = parse_feed("ktnbytes", xml)[0]
        self.assertEqual(item["torrent_url"], "https://ktnbytes.com/post/file.torrent")
        self.assertEqual(item["detail_url"], "https://ktnbytes.com/post")

    def test_nyaa_link_and_namespace_metadata(self):
        xml = b"""<rss xmlns:nyaa="https://nyaa.si/xmlns/nyaa"><channel><item>
            <title>VCB Show</title><guid>https://nyaa.si/view/123</guid>
            <link>https://nyaa.si/download/123.torrent</link>
            <nyaa:infoHash>ABCDEF</nyaa:infoHash><nyaa:size>2 GiB</nyaa:size>
            </item></channel></rss>"""
        item = parse_feed("vcb-studio", xml)[0]
        self.assertEqual(item["detail_url"], "https://nyaa.si/view/123")
        self.assertEqual(item["torrent_url"], "https://nyaa.si/download/123.torrent")
        self.assertEqual(item["info_hash"], "abcdef")

    def test_detail_description_is_text(self):
        html = '<div id="torrent-description"><b>Release notes</b><p>Season 1</p></div>'
        self.assertIn("Season 1", extract_description("vcb-studio", html))


class ResourceStoreTests(unittest.TestCase):
    def test_retry_selector_only_includes_failed_and_waiting_config(self):
        from scripts.debug.rss_title import list_retry_ids
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (patch.object(connection, "DB_PATH", root / "mam.sqlite3"),
                  patch.object(connection, "LEGACY_RESOURCE_DB", root / "missing.sqlite3")):
                for number, status in enumerate(("failed", "waiting_config", "complete", "unresolved"), 1):
                    item = {"source": "ktnbytes", "source_id": str(number), "index_type": "tmdb",
                            "title": f"Show {number}", "published_at": "", "detail_url": "https://example.com/post",
                            "torrent_url": "https://example.com/file.torrent", "rss_description": "",
                            "info_hash": "", "size_label": ""}
                    record = resources.upsert_feed_item(item)
                    resource_recognitions.save(record["id"], {"rule_version": 1}, status, [])
                self.assertEqual(list_retry_ids(root / "mam.sqlite3"), [1, 2])

    def test_recognition_save_replaces_only_derived_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (patch.object(connection, "DB_PATH", root / "mam.sqlite3"),
                  patch.object(connection, "LEGACY_RESOURCE_DB", root / "missing.sqlite3")):
                item = {"source": "ktnbytes", "source_id": "resource-1", "index_type": "tmdb",
                        "title": "Show S02 01-13", "published_at": "", "detail_url": "https://example.com/post",
                        "torrent_url": "https://example.com/file.torrent", "rss_description": "",
                        "info_hash": "", "size_label": ""}
                record = resources.upsert_feed_item(item)
                resources.update_resource(record["id"], status="complete", torrent_path="/saved.torrent")
                candidate = {"bangumi_id": 11, "index_id": 22, "index_season": 2,
                             "media_type": "TV", "decision": "retained", "reason": "match", "match_count": 13}
                resource_recognitions.save(record["id"], {"rule_version": 1}, "complete", [candidate])
                replacement = {**candidate, "bangumi_id": 33, "match_count": 12}
                resource_recognitions.save(record["id"], {"rule_version": 3}, "complete", [replacement])
                stored = resource_recognitions.get(record["id"])
                self.assertEqual(stored["title_snapshot"]["rule_version"], 3)
                self.assertEqual([item["bangumi_id"] for item in stored["candidates"]], [33])
                original = resources.get_resource(record["id"])
                self.assertEqual(original["title"], "Show S02 01-13")
                self.assertEqual(original["torrent_path"], "/saved.torrent")

    def test_upsert_retains_collected_data_and_searches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (patch.object(resources, "TORRENT_ROOT", root / "torrents"),
                  patch.object(connection, "DB_PATH", root / "mam.sqlite3"),
                  patch.object(connection, "LEGACY_RESOURCE_DB", root / "missing.sqlite3")):
                item = {
                    "source": "ktnbytes", "source_id": "https://ktnbytes.com/post",
                    "title": "Show S01", "published_at": "2026-09-27",
                    "detail_url": "https://ktnbytes.com/post",
                    "torrent_url": "https://ktnbytes.com/post/file.torrent",
                    "rss_description": "", "info_hash": "", "size_label": "",
                }
                record = resources.upsert_feed_item(item)
                resources.update_resource(record["id"], status="complete", detail_fetched=1,
                                          torrent_files=[{"name": "Show/01.mkv"}])
                again = resources.upsert_feed_item(item)
                self.assertEqual(again["status"], "complete")
                self.assertEqual(resources.list_resources(q="Show")["total"], 1)
                self.assertEqual(resources.get_resource(record["id"])["torrent_files"][0]["name"], "Show/01.mkv")


if __name__ == "__main__":
    unittest.main()
