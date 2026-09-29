"""Database engine and first-run migration of the existing resource database."""

import os
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
        # create_all does not add columns to an existing resource table.
        with engine.begin() as connection:
            columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(resources)")}
            if "index_type" not in columns:
                connection.exec_driver_sql(
                    "ALTER TABLE resources ADD COLUMN index_type TEXT NOT NULL DEFAULT 'tmdb'"
                )
                connection.exec_driver_sql(
                    "UPDATE resources SET index_type='tvdb' WHERE source='vcb-studio'"
                )
        _engine, _engine_path = engine, path
        return engine


def new_session() -> Session:
    return Session(get_engine())
