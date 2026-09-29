"""Existing SQLite JSON columns migrate without losing application data."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.db import connection, resource_recognitions, resource_sources, resources, torrents
from backend.db.models import Resource, ResourceRecognition, ResourceSource, TorrentCard


class RelationalMigrationTests(unittest.TestCase):
    def test_new_database_has_no_json_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "mam.sqlite3"
            with (patch.object(connection, "DB_PATH", database),
                  patch.object(connection, "LEGACY_RESOURCE_DB", database.with_name("missing.sqlite3"))):
                with connection.get_engine().connect() as conn:
                    tables = [row[0] for row in conn.exec_driver_sql(
                        "SELECT name FROM sqlite_master WHERE type='table'")]
                    json_columns = [(table, row[1]) for table in tables
                                    for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")
                                    if row[2].upper() == "JSON"]
                self.assertEqual(json_columns, [])
                connection._engine.dispose()
                connection._engine = None
                connection._engine_path = None

    def test_old_source_and_card_schemas_gain_relational_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "mam.sqlite3"
            with sqlite3.connect(database) as conn:
                conn.execute("CREATE TABLE resource_sources (name TEXT PRIMARY KEY, rss_url TEXT NOT NULL, downloadtag JSON NOT NULL, index_type TEXT NOT NULL)")
                conn.execute("INSERT INTO resource_sources VALUES (?, ?, ?, ?)",
                             ("test", "https://feed", json.dumps({"tag": "link", "attribute": None,
                                                                    "future": True}), "tmdb"))
                conn.execute("""CREATE TABLE torrent_cards (
                    id INTEGER PRIMARY KEY, info_hash TEXT NOT NULL UNIQUE,
                    torrent_name TEXT NOT NULL DEFAULT '', show_name TEXT NOT NULL DEFAULT '',
                    bgm_rating REAL NOT NULL DEFAULT 0, poster_url TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'downloading', created_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT '', encoding_group TEXT NOT NULL DEFAULT '',
                    video_codec TEXT NOT NULL DEFAULT '', bangumi_ids JSON NOT NULL,
                    processing JSON, extra_data JSON NOT NULL)""")
                conn.execute("INSERT INTO torrent_cards (id, info_hash, bangumi_ids, processing, extra_data) VALUES (?, ?, ?, ?, ?)",
                             (1, "abc", json.dumps([42]),
                              json.dumps({"mode": "tv", "files": [{"action": "copy"}]}),
                              json.dumps({"future_field": "kept"})))
            with (patch.object(connection, "DB_PATH", database),
                  patch.object(connection, "LEGACY_RESOURCE_DB", root / "missing.sqlite3"),
                  patch.object(torrents, "LEGACY_FILE", root / "missing.json")):
                self.assertEqual(resource_sources.list_sources()["test"]["downloadtag"],
                                 {"tag": "link", "attribute": None, "future": True})
                card = torrents.list_torrents()[0]
                self.assertEqual(card["bangumi_ids"], [42])
                self.assertEqual(card["processing"]["files"], [{"action": "copy"}])
                self.assertEqual(card["future_field"], "kept")
                connection._engine.dispose()
                connection._engine = None
                connection._engine_path = None
                self.assertEqual(torrents.list_torrents()[0]["bangumi_ids"], [42])
                connection._engine.dispose()
                connection._engine = None
                connection._engine_path = None

    def test_existing_structured_columns_are_migrated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (patch.object(connection, "DB_PATH", root / "mam.sqlite3"),
                  patch.object(connection, "LEGACY_RESOURCE_DB", root / "missing.sqlite3"),
                  patch.object(torrents, "LEGACY_FILE", root / "missing.json")):
                with connection.new_session() as session, session.begin():
                    session.add(Resource(id=1, source="test", source_id="one",
                                         title="Show", index_type="tmdb"))
                    session.add(ResourceSource(name="test", rss_url="https://feed",
                                               download_tag="link", index_type="tmdb"))
                    session.add(ResourceRecognition(resource_id=1, status="complete"))
                    session.add(TorrentCard(id=1, info_hash="abc", status="downloading"))
                with connection.get_engine().begin() as conn:
                    conn.exec_driver_sql("ALTER TABLE resources ADD COLUMN torrent_files JSON")
                    conn.exec_driver_sql("UPDATE resources SET torrent_files=? WHERE id=1",
                                         (json.dumps([{"name": "Show/01.mkv", "size": 1024}]),))
                    conn.exec_driver_sql("ALTER TABLE resource_sources ADD COLUMN downloadtag JSON")
                    conn.exec_driver_sql("UPDATE resource_sources SET downloadtag=? WHERE name='test'",
                                         (json.dumps({"tag": "enclosure", "attribute": "url"}),))
                    conn.exec_driver_sql("ALTER TABLE resource_recognitions ADD COLUMN title_snapshot JSON")
                    conn.exec_driver_sql("UPDATE resource_recognitions SET title_snapshot=? WHERE resource_id=1",
                                         (json.dumps({"rule_version": 3, "video_codec": "HEVC"}),))
                    for column in ("bangumi_ids", "processing", "extra_data"):
                        conn.exec_driver_sql(f"ALTER TABLE torrent_cards ADD COLUMN {column} JSON")
                    conn.exec_driver_sql("UPDATE torrent_cards SET bangumi_ids=?, processing=?, extra_data=? WHERE id=1",
                                         (json.dumps([42]),
                                          json.dumps({"mode": "tv", "files": [{"action": "hardlink", "future": True}]}),
                                          json.dumps({"future_field": "saved"})))
                connection._engine.dispose()
                connection._engine = None
                connection._engine_path = None
                connection.get_engine()
                self.assertEqual(resources.get_resource(1)["torrent_files"],
                                 [{"name": "Show/01.mkv", "size": 1024}])
                self.assertEqual(resource_sources.list_sources()["test"]["downloadtag"],
                                 {"tag": "enclosure", "attribute": "url"})
                self.assertEqual(resource_recognitions.get(1)["title_snapshot"]["video_codec"],
                                 "HEVC")
                card = torrents.list_torrents()[0]
                self.assertEqual(card["bangumi_ids"], [42])
                self.assertEqual(card["processing"]["files"],
                                 [{"action": "hardlink", "future": True}])
                self.assertEqual(card["future_field"], "saved")
                with connection.get_engine().connect() as conn:
                    for table, old_column in (("resources", "torrent_files"),
                                              ("resource_sources", "downloadtag"),
                                              ("resource_recognitions", "title_snapshot"),
                                              ("torrent_cards", "processing")):
                        columns = {row[1] for row in conn.exec_driver_sql(
                            f"PRAGMA table_info({table})")}
                        self.assertNotIn(old_column, columns)
                connection._engine.dispose()
                connection._engine = None
                connection._engine_path = None


if __name__ == "__main__":
    unittest.main()
