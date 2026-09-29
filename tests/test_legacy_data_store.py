"""Migration and update behavior for the SQLite-backed legacy data."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import data
from backend.db import connection


class LegacyDataStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.db_patch = patch.object(connection, "DB_PATH", root / "mam.sqlite3")
        self.sub_patch = patch.object(data, "_SUBS_FILE", root / "subscriptions.json")
        self.map_patch = patch.object(data, "_MAP_FILE", root / "bangumi_mikan_map.json")
        self.hist_patch = patch.object(data, "_HIST_FILE", root / "download_history.json")
        for item in (self.db_patch, self.sub_patch, self.map_patch, self.hist_patch):
            item.start()
        data._bangumi_mikan_map = None

    def tearDown(self):
        data._bangumi_mikan_map = None
        for item in (self.hist_patch, self.map_patch, self.sub_patch, self.db_patch):
            item.stop()
        if connection._engine is not None:
            connection._engine.dispose()
            connection._engine = None
            connection._engine_path = None
        self.temp.cleanup()

    def test_legacy_subscription_import_and_mutation(self):
        old = [{"bangumi_id": 12, "name": "Show", "rss_url": "https://feed", "bgm_season": 2}]
        data._SUBS_FILE.write_text(json.dumps(old))
        imported = data.list_subscriptions()
        self.assertEqual(imported[0]["bgm"]["season"], 2)
        self.assertEqual(json.loads(data._SUBS_FILE.read_text()), old)
        self.assertTrue(data.set_subscription_rss_offset(12, "primary", 3))
        self.assertTrue(data.update_subscription(12, {"active": 0}))
        self.assertEqual(data.list_subscriptions()[0]["primary"]["offset"], 3)
        self.assertEqual(data.list_subscriptions()[0]["active"], 0)
        data.add_subscription("Show", "https://feed", 12, 0, "")
        self.assertEqual(data.list_subscriptions()[0]["primary"]["offset"], 3)
        data.add_subscription("Show", "https://other", 12, 0, "")
        self.assertNotIn("offset", data.list_subscriptions()[0]["primary"])
        self.assertTrue(data.remove_subscription(12))
        self.assertEqual(data.list_subscriptions(), [])
        self.assertEqual(len(json.loads(data._SUBS_FILE.read_text())), 1)

    def test_mapping_overrides_survive_upstream_refresh(self):
        legacy = {"12": {"name": "Show", "mikan_id": 4, "tmdb_id": 5},
                  "13": {"name": "Removed"}}
        data._MAP_FILE.write_text(json.dumps(legacy))
        self.assertEqual(data.mapping_count(), 2)
        self.assertTrue(data.set_mikan_id(12, 9))
        data.replace_mappings({"12": {"name": "Updated", "mikan_id": 6, "tmdb_id": 8},
                               "14": {"name": "New"}})
        self.assertEqual(data.get_mikan_id(12), 9)
        self.assertEqual(data.get_tmdb_id(12), 8)
        self.assertEqual(data.get_bangumi_name(12), "Updated")
        self.assertEqual(data.mapping_count(), 2)
        self.assertEqual(json.loads(data._MAP_FILE.read_text()), legacy)
        data._bangumi_mikan_map = None
        self.assertEqual(data.get_mikan_id(12), 9)

    def test_online_bangumi_selection_persists_and_can_be_assigned_mikan(self):
        data._MAP_FILE.write_text('{}')
        data.add_mapping(42, '中文标题', 'Original Title')
        self.assertEqual(data.get_bangumi_name(42), '中文标题')
        self.assertEqual(data.search_by_name('中文')[0]['bangumi_id'], 42)
        self.assertTrue(data.set_mikan_id(42, 17))
        data.replace_mappings({'12': {'name': 'Upstream'}})
        self.assertEqual(data.get_bangumi_name(42), '中文标题')
        self.assertEqual(data.get_mikan_id(42), 17)

    def test_invalid_legacy_file_can_be_retried(self):
        data._SUBS_FILE.write_text('{"bad": true}')
        with self.assertRaises(ValueError):
            data.list_subscriptions()
        data._SUBS_FILE.write_text('[]')
        self.assertEqual(data.list_subscriptions(), [])

    def test_download_history_import_and_updates(self):
        legacy = {"episodes": {"12": {"1": {"source": "primary", "tmdb_ep": 7}}},
                  "future_field": {"kept": True}}
        data._HIST_FILE.write_text(json.dumps(legacy))
        self.assertTrue(data.is_downloaded(12, 1))
        self.assertEqual(data.get_episode_source(12, 1), "primary")
        data.mark_downloaded(12, 1, "https://feed", "guid", "backup")
        self.assertEqual(data.get_all_episodes(12)["1"]["tmdb_ep"], 7)
        self.assertEqual(data.increment_fail_count(12, 1), 1)
        self.assertEqual(data.get_fail_count(12, 1), 1)
        data.reset_fail_count(12, 1)
        self.assertEqual(data.get_fail_count(12, 1), 0)
        self.assertTrue(data.set_episode_overrides(12, 1, tmdb_ep=8))
        self.assertEqual(data.get_all_episodes(12)["1"]["tmdb_ep"], 8)
        self.assertEqual(data._load_hist()["future_field"], {"kept": True})
        self.assertTrue(data.remove_episode_record(12, 1))
        self.assertEqual(data.clear_download_history(12), 0)
        self.assertEqual(json.loads(data._HIST_FILE.read_text()), legacy)


if __name__ == "__main__":
    unittest.main()
