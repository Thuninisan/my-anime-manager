"""Torrent card migration and resumable processing persistence."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.db import connection, torrents


class TorrentStoreTests(unittest.TestCase):
    def test_fontinass_checkpoint_survives_media_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (patch.object(connection, "DB_PATH", root / "mam.sqlite3"),
                  patch.object(connection, "LEGACY_RESOURCE_DB", root / "missing.sqlite3"),
                  patch.object(torrents, "LEGACY_FILE", root / "missing.json")):
                torrents.save_torrent({"info_hash": "font-test", "processing": {"files": [
                    {"action": "copy", "target_path": "/library/episode.ass"},
                ]}, "other_data": "preserved"})
                state = {"status": "processing", "ready": True, "files": [{
                    "path": "/library/episode.ass", "status": "processing", "output_hash": "expected",
                }]}
                torrents.save_fontinass("font-test", state)
                card = torrents.get_torrent("font-test")
                self.assertEqual(card["fontinass"], state)
                self.assertEqual(card["other_data"], "preserved")
                self.assertEqual(card["processing"]["files"][0]["action"], "copy")
                torrents.finish_torrent("font-test", "completed")
                card = torrents.get_torrent("font-test")
                self.assertEqual(card["fontinass"], state)
                self.assertNotIn("processing", card)

    def test_legacy_import_upsert_and_finish(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_file = root / "torrents.json"
            old_file.write_text(json.dumps({"version": 1, "torrents": [
                {"info_hash": "abc", "torrent_name": "Show", "status": "downloading",
                 "created_at": "original", "updated_at": "original",
                 "bangumi_ids": [42], "processing": {"files": [{"action": "hardlink"}]},
                 "future_field": "preserved"},
                {"info_hash": "def", "torrent_name": "Other", "status": "completed",
                 "created_at": "second", "updated_at": "second"},
            ]}), encoding="utf-8")
            with (patch.object(connection, "DB_PATH", root / "mam.sqlite3"),
                  patch.object(connection, "LEGACY_RESOURCE_DB", root / "missing.sqlite3"),
                  patch.object(torrents, "LEGACY_FILE", old_file)):
                self.assertEqual(len(torrents.list_torrents()), 2)
                self.assertEqual(len(torrents.list_torrents()), 2)
                pending = torrents.list_pending_torrents()
                self.assertEqual([item["info_hash"] for item in pending], ["abc"])
                self.assertEqual(pending[0]["future_field"], "preserved")

                replacement = {"info_hash": "abc", "torrent_name": "Updated",
                               "status": "downloading", "created_at": "new",
                               "updated_at": "new", "processing": {"files": []}}
                torrents.save_torrent(replacement)
                self.assertEqual(replacement["created_at"], "original")
                self.assertEqual(torrents.list_pending_torrents()[0]["torrent_name"], "Updated")

                torrents.finish_torrent("abc", "completed")
                self.assertEqual(torrents.list_pending_torrents(), [])
                card = torrents.list_torrents()[0]
                self.assertEqual(card["status"], "completed")
                self.assertNotIn("processing", card)
                self.assertTrue(old_file.is_file())


if __name__ == "__main__":
    unittest.main()
