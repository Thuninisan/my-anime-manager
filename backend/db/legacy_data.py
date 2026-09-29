"""SQLite storage and one-time imports for subscriptions and Bangumi mappings."""

import json
import logging
from pathlib import Path

from sqlalchemy import select, func
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm.attributes import flag_modified

from .connection import new_session
from .models import BangumiMapping, JsonDocument, LegacyImport, Subscription

logger = logging.getLogger(__name__)
SUB_IMPORT = "subscriptions.json:v1"
MAP_IMPORT = "bangumi_mikan_map.json:v1"
HISTORY_NAME = "download_history"
HISTORY_IMPORT = "download_history.json:v1"


def _document(path: Path, kind: type):
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, kind):
        raise ValueError(f"Invalid legacy data document: {path}")
    return document


def ensure_history(path: Path) -> None:
    with new_session() as session, session.begin():
        if session.get(LegacyImport, HISTORY_IMPORT):
            return
        document = _document(path, dict) if path.is_file() else {}
        if "episodes" in document and not isinstance(document["episodes"], dict):
            raise ValueError(f"Invalid legacy download history: {path}")
        session.execute(insert(JsonDocument).values(name=HISTORY_NAME, data=document)
                        .on_conflict_do_nothing(index_elements=[JsonDocument.name]))
        session.add(LegacyImport(name=HISTORY_IMPORT))
        logger.info("Imported download history from %s", path)


def get_history(path: Path) -> dict:
    ensure_history(path)
    with new_session() as session:
        row = session.get(JsonDocument, HISTORY_NAME)
        return row.data if row else {}


def mutate_history(path: Path, operation):
    ensure_history(path)
    with new_session() as session, session.begin():
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row = session.get(JsonDocument, HISTORY_NAME)
        document = row.data if row else {}
        result = operation(document)
        if row:
            flag_modified(row, "data")
        else:
            session.add(JsonDocument(name=HISTORY_NAME, data=document))
        return result


def ensure_subscriptions(path: Path, migrate):
    with new_session() as session, session.begin():
        if session.get(LegacyImport, SUB_IMPORT):
            return
        if path.is_file():
            records = _document(path, list)
            migrate(records)
            for position, record in enumerate(records):
                if not isinstance(record, dict) or not isinstance(record.get("bangumi_id"), int):
                    raise ValueError(f"Invalid subscription record in {path}")
                session.execute(insert(Subscription).values(
                    bangumi_id=record["bangumi_id"], position=position, data=record,
                ).on_conflict_do_nothing(index_elements=[Subscription.bangumi_id]))
            logger.info("Imported %d subscriptions from %s", len(records), path)
        session.add(LegacyImport(name=SUB_IMPORT))


def list_subscriptions(path: Path, migrate) -> list[dict]:
    ensure_subscriptions(path, migrate)
    with new_session() as session:
        return [row.data for row in session.scalars(
            select(Subscription).order_by(Subscription.position, Subscription.bangumi_id))]


def mutate_subscription(path: Path, migrate, bangumi_id: int, operation):
    ensure_subscriptions(path, migrate)
    with new_session() as session, session.begin():
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row = session.get(Subscription, bangumi_id)
        before = dict(row.data) if row else None
        after, result = operation(before)
        if after is None:
            if row:
                session.delete(row)
        elif row:
            row.data = after
        else:
            position = session.scalar(select(func.max(Subscription.position)))
            session.add(Subscription(bangumi_id=bangumi_id, position=(position if position is not None else -1) + 1, data=after))
        return result


def ensure_mappings(path: Path):
    with new_session() as session, session.begin():
        if session.get(LegacyImport, MAP_IMPORT):
            return
        if not path.is_file():
            raise FileNotFoundError(f"Bangumi-Mikan mapping not found at {path}")
        records = _document(path, dict)
        for key, value in records.items():
            if not isinstance(value, dict):
                raise ValueError(f"Invalid mapping record in {path}: {key}")
            bangumi_id = int(key)
            session.execute(insert(BangumiMapping).values(
                bangumi_id=bangumi_id, tmdb_id=value.get("tmdb_id"),
                tvdb_id=value.get("tvdb_id"), data=value, overrides={},
            ).on_conflict_do_nothing(index_elements=[BangumiMapping.bangumi_id]))
        session.add(LegacyImport(name=MAP_IMPORT))
        logger.info("Imported %d Bangumi mappings from %s", len(records), path)


def list_mappings(path: Path) -> dict[int, dict]:
    ensure_mappings(path)
    with new_session() as session:
        return {row.bangumi_id: row.data for row in session.scalars(select(BangumiMapping))}


def mapping_count(path: Path) -> int:
    ensure_mappings(path)
    with new_session() as session:
        return session.scalar(select(func.count()).select_from(BangumiMapping)) or 0


def set_mapping_fields(path: Path, bangumi_id: int, fields: dict) -> bool:
    ensure_mappings(path)
    with new_session() as session, session.begin():
        row = session.get(BangumiMapping, bangumi_id)
        if row is None:
            return False
        row.data = {**row.data, **fields}
        row.overrides = {**row.overrides, **fields}
        row.tmdb_id = row.data.get("tmdb_id")
        row.tvdb_id = row.data.get("tvdb_id")
        return True


def add_mapping(path: Path, bangumi_id: int, name: str, name_original: str = "") -> None:
    """Persist a Bangumi search selection so the normal Mikan flow can use it."""
    ensure_mappings(path)
    fields = {"name": name, "name_original": name_original}
    with new_session() as session, session.begin():
        session.execute(insert(BangumiMapping).values(
            bangumi_id=bangumi_id, data=fields, overrides=fields,
        ).on_conflict_do_nothing(index_elements=[BangumiMapping.bangumi_id]))


def replace_mappings(path: Path, records: dict):
    """Replace upstream dataset atomically, preserving manual field overrides."""
    if not isinstance(records, dict) or not records:
        raise ValueError("Empty or invalid Bangumi mapping dataset")
    ensure_mappings(path)
    normalized = {}
    for key, value in records.items():
        if not isinstance(value, dict) or not isinstance(value.get("name"), str):
            raise ValueError(f"Invalid Bangumi mapping entry: {key}")
        normalized[int(key)] = value
    with new_session() as session, session.begin():
        current = {row.bangumi_id: row for row in session.scalars(select(BangumiMapping))}
        for bangumi_id, upstream in normalized.items():
            row = current.pop(bangumi_id, None)
            overrides = row.overrides if row else {}
            merged = {**upstream, **overrides}
            if row is None:
                session.add(BangumiMapping(bangumi_id=bangumi_id, data=merged,
                                           overrides={}, tmdb_id=merged.get("tmdb_id"),
                                           tvdb_id=merged.get("tvdb_id")))
            else:
                row.data = merged
                row.tmdb_id = merged.get("tmdb_id")
                row.tvdb_id = merged.get("tvdb_id")
        # Retain user-corrected entries removed by upstream, including their original metadata.
        for row in current.values():
            if not row.overrides:
                session.delete(row)
