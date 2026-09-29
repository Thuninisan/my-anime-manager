"""Database engine and first-run migration of the existing resource database."""

import os
import json
import sqlite3
import tempfile
import threading
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session

from ..utils.paths import USER_DATA_DIR
from .models import Base

DB_PATH = USER_DATA_DIR / "mam.sqlite3"
LEGACY_RESOURCE_DB = USER_DATA_DIR / "resource_monitor" / "resources.sqlite3"
_engine: Engine | None = None
_engine_path: Path | None = None
_engine_lock = threading.Lock()


def _copy_legacy_database(path: Path) -> None:
    """Keep the old database intact and install a consistent SQLite backup."""
    if path.exists() or not LEGACY_RESOURCE_DB.exists():
        return
    fd, temporary = tempfile.mkstemp(prefix=".mam-migrate-", suffix=".sqlite3", dir=path.parent)
    os.close(fd)
    try:
        with sqlite3.connect(LEGACY_RESOURCE_DB) as source, sqlite3.connect(temporary) as target:
            source.backup(target)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def get_engine() -> Engine:
    global _engine, _engine_path
    path = Path(DB_PATH)
    if _engine is not None and _engine_path == path:
        return _engine
    with _engine_lock:
        if _engine is not None and _engine_path == path:
            return _engine
        if _engine is not None:
            _engine.dispose()
        path.parent.mkdir(parents=True, exist_ok=True)
        _copy_legacy_database(path)
        engine = create_engine(f"sqlite:///{path}", connect_args={"timeout": 30})

        @event.listens_for(engine, "connect")
        def _configure_sqlite(dbapi_connection, _record):
            dbapi_connection.execute("PRAGMA journal_mode=WAL")
            dbapi_connection.execute("PRAGMA busy_timeout=30000")

        Base.metadata.create_all(engine)
        # create_all does not alter existing tables. Move the former JSON
        # columns into typed columns and child rows before serving requests.
        with engine.begin() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(resources)")}
            if "index_type" not in columns:
                connection.exec_driver_sql(
                    "ALTER TABLE resources ADD COLUMN index_type TEXT NOT NULL DEFAULT 'tmdb'"
                )
                connection.exec_driver_sql(
                    "UPDATE resources SET index_type='tvdb' WHERE source='vcb-studio'"
                )
            if "torrent_files" in columns:
                from .structured_values import replace as replace_structured
                with Session(bind=connection) as migration_session:
                    for resource_id, raw in connection.exec_driver_sql(
                            "SELECT id, torrent_files FROM resources"):
                        for position, item in enumerate(json.loads(raw) if raw else []):
                            connection.exec_driver_sql(
                                "INSERT OR REPLACE INTO resource_torrent_files (resource_id, position, name) VALUES (?, ?, ?)",
                                (resource_id, position, item["name"]))
                            replace_structured(migration_session, "resource", resource_id,
                                               f"torrent_file:{position}",
                                               {key: value for key, value in item.items() if key != "name"})
                    migration_session.flush()
                connection.exec_driver_sql("ALTER TABLE resources DROP COLUMN torrent_files")
            source_columns = {row[1] for row in connection.exec_driver_sql(
                "PRAGMA table_info(resource_sources)")}
            if "downloadtag" in source_columns:
                if "download_tag" not in source_columns:
                    connection.exec_driver_sql("ALTER TABLE resource_sources ADD COLUMN download_tag TEXT NOT NULL DEFAULT ''")
                if "download_attribute" not in source_columns:
                    connection.exec_driver_sql("ALTER TABLE resource_sources ADD COLUMN download_attribute TEXT")
                from .structured_values import replace as replace_structured
                with Session(bind=connection) as migration_session:
                    for name, raw in connection.exec_driver_sql(
                            "SELECT name, downloadtag FROM resource_sources"):
                        tag = json.loads(raw)
                        connection.exec_driver_sql(
                            "UPDATE resource_sources SET download_tag=?, download_attribute=? WHERE name=?",
                            (tag.get("tag", ""), tag.get("attribute"), name))
                        replace_structured(migration_session, "resource_source", name,
                                           "downloadtag_extra",
                                           {key: value for key, value in tag.items()
                                            if key not in {"tag", "attribute"}})
                    migration_session.flush()
                connection.exec_driver_sql("ALTER TABLE resource_sources DROP COLUMN downloadtag")
            recognition_columns = {row[1] for row in connection.exec_driver_sql(
                "PRAGMA table_info(resource_recognitions)")}
            if "title_snapshot" in recognition_columns:
                from .structured_values import replace as replace_structured
                with Session(bind=connection) as migration_session:
                    for resource_id, raw in connection.exec_driver_sql(
                            "SELECT resource_id, title_snapshot FROM resource_recognitions"):
                        replace_structured(migration_session, "recognition", resource_id,
                                           "title_snapshot", json.loads(raw))
                    migration_session.flush()
                connection.exec_driver_sql("ALTER TABLE resource_recognitions DROP COLUMN title_snapshot")
            card_columns = {row[1] for row in connection.exec_driver_sql(
                "PRAGMA table_info(torrent_cards)")}
            if "bangumi_ids" in card_columns:
                from .structured_values import replace as replace_structured
                if "processing_mode" not in card_columns:
                    connection.exec_driver_sql("ALTER TABLE torrent_cards ADD COLUMN processing_mode TEXT")
                if "replace_bangumi_id" not in card_columns:
                    connection.exec_driver_sql("ALTER TABLE torrent_cards ADD COLUMN replace_bangumi_id INTEGER")
                if "processing_present" not in card_columns:
                    connection.exec_driver_sql("ALTER TABLE torrent_cards ADD COLUMN processing_present INTEGER NOT NULL DEFAULT 0")
                with Session(bind=connection) as migration_session:
                    rows = connection.exec_driver_sql(
                        "SELECT id, bangumi_ids, processing, extra_data FROM torrent_cards").all()
                    for card_id, raw_ids, raw_processing, raw_extra in rows:
                        for position, bangumi_id in enumerate(json.loads(raw_ids or "[]")):
                            connection.exec_driver_sql(
                                "INSERT INTO torrent_card_bangumi VALUES (?, ?, ?)",
                                (card_id, position, bangumi_id))
                        processing = json.loads(raw_processing) if raw_processing else None
                        if processing is not None:
                            connection.exec_driver_sql(
                                "UPDATE torrent_cards SET processing_mode=?, replace_bangumi_id=?, processing_present=1 WHERE id=?",
                                (processing.get("mode"), processing.get("replace_bangumi_id"), card_id))
                            for position, operation in enumerate(processing.get("files", [])):
                                connection.exec_driver_sql(
                                    "INSERT INTO torrent_card_operations VALUES (?, ?, ?, ?, ?, ?, ?)",
                                    (card_id, position, operation.get("torrent_path"),
                                     operation.get("source_path"), operation.get("target_path"),
                                     operation.get("action"), operation.get("bangumi_sort")))
                                replace_structured(migration_session, "torrent_card", card_id,
                                                   f"operation:{position}",
                                                   {key: value for key, value in operation.items()
                                                    if key not in {"torrent_path", "source_path",
                                                                   "target_path", "action", "bangumi_sort"}})
                            extra_processing = {key: value for key, value in processing.items()
                                                if key not in {"mode", "replace_bangumi_id", "files"}}
                            replace_structured(migration_session, "torrent_card", card_id,
                                               "processing_extra", extra_processing)
                        replace_structured(migration_session, "torrent_card", card_id,
                                           "extra_data", json.loads(raw_extra or "{}"))
                    migration_session.flush()
                for column in ("bangumi_ids", "processing", "extra_data"):
                    connection.exec_driver_sql(f"ALTER TABLE torrent_cards DROP COLUMN {column}")
        _engine, _engine_path = engine, path
        return engine


def new_session() -> Session:
    return Session(get_engine())
