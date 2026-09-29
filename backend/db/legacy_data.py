"""SQLite storage and one-time imports for subscriptions and Bangumi mappings."""

import json
import logging
from pathlib import Path

from sqlalchemy import select, func
from sqlalchemy.dialects.sqlite import insert

from .connection import new_session
from . import structured_values
from .models import (BangumiMapping, BangumiMappingOverride, LegacyImport, Subscription,
                     SubscriptionFeed, SubscriptionFeedRule)

logger = logging.getLogger(__name__)
SUB_IMPORT = "subscriptions.json:v1"
SUB_TABLE_IMPORT = "subscriptions:relational:v2"
MAP_IMPORT = "bangumi_mikan_map.json:v1"
MAP_TABLE_IMPORT = "bangumi_mappings:relational:v2"


def _document(path: Path, kind: type):
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, kind):
        raise ValueError(f"Invalid legacy data document: {path}")
    return document


def _subscription_record(session, row: Subscription) -> dict:
    record = {"bangumi_id": row.bangumi_id, "name": row.name,
              "download_path": row.download_path, "active": row.active,
              "created_at": row.created_at}
    record.update(structured_values.read(session, "subscription", row.bangumi_id,
                                         "extras", {}))
    if row.updated_at is not None:
        record["updated_at"] = row.updated_at
    if row.series_name is not None:
        record["series_name"] = row.series_name
    bgm_extras = structured_values.read(session, "subscription", row.bangumi_id,
                                        "bgm_extras", {})
    if bgm_extras or any(value is not None for value in (
            row.bgm_season, row.bgm_sort_start, row.bgm_sort_end,
            row.bgm_subject_name, row.bgm_series_name, row.bgm_rating, row.bgm_air_date)):
        record["bgm"] = {"season": row.bgm_season or 1,
                         "sortrange": [row.bgm_sort_start or 0, row.bgm_sort_end or 0],
                         "subject_name": row.bgm_subject_name or "",
                         "series_name": row.bgm_series_name or "",
                         "rating": row.bgm_rating or 0, "air_date": row.bgm_air_date or ""}
        record["bgm"].update(bgm_extras)
    for kind in ("tmdb", "tvdb"):
        season = getattr(row, f"{kind}_season")
        source_id = getattr(row, f"{kind}_id")
        offset = getattr(row, f"{kind}_ep_offset")
        extras = structured_values.read(session, "subscription", row.bangumi_id,
                                        f"{kind}_extras", {})
        if extras or any(value is not None for value in (season, source_id, offset)):
            record[kind] = {"id": source_id or 0, "season": season, "ep_offset": offset or 0}
            record[kind].update(extras)
    for kind in ("primary", "backup"):
        feed = session.get(SubscriptionFeed, (row.bangumi_id, kind))
        if feed is None:
            continue
        value = {"rss_url": feed.rss_url, "subgroup_id": feed.subgroup_id,
                 "subgroup_name": feed.subgroup_name}
        if feed.offset is not None:
            value["offset"] = feed.offset
        for rule_type in ("filter_tags", "exclude_patterns"):
            value[rule_type] = [rule.value for rule in session.scalars(
                select(SubscriptionFeedRule).where(
                    SubscriptionFeedRule.bangumi_id == row.bangumi_id,
                    SubscriptionFeedRule.kind == kind,
                    SubscriptionFeedRule.rule_type == rule_type,
                ).order_by(SubscriptionFeedRule.position))]
        value.update(structured_values.read(
            session, "subscription", row.bangumi_id, f"{kind}_extras", {}))
        record[kind] = value
    return record


def _write_subscription(session, record: dict, position: int) -> None:
    fields_by_group = {
        "record": {"bangumi_id", "name", "series_name", "download_path", "active",
                   "created_at", "updated_at", "bgm", "tmdb", "tvdb", "primary", "backup"},
        "bgm": {"season", "sortrange", "subject_name", "series_name", "rating", "air_date"},
        "tmdb": {"id", "season", "ep_offset"},
        "tvdb": {"id", "season", "ep_offset"},
        "primary": {"rss_url", "subgroup_id", "subgroup_name", "offset",
                    "filter_tags", "exclude_patterns"},
        "backup": {"rss_url", "subgroup_id", "subgroup_name", "offset",
                   "filter_tags", "exclude_patterns"},
    }
    structured_values.replace(session, "subscription", record["bangumi_id"], "extras",
                              {key: value for key, value in record.items()
                               if key not in fields_by_group["record"]})
    for group in ("bgm", "tmdb", "tvdb", "primary", "backup"):
        structured_values.replace(session, "subscription", record["bangumi_id"],
                                  f"{group}_extras",
                                  {key: value for key, value in (record.get(group) or {}).items()
                                   if key not in fields_by_group[group]})
    bangumi_id = record["bangumi_id"]
    row = session.get(Subscription, bangumi_id)
    if row is None:
        row = Subscription(bangumi_id=bangumi_id, position=position)
        session.add(row)
    bgm = record.get("bgm") or {}
    sort_range = list(bgm.get("sortrange") or []) + [None, None]
    tmdb = record.get("tmdb") or {}
    tvdb = record.get("tvdb") or {}
    fields = {
        "name": record.get("name", ""), "series_name": record.get("series_name"),
        "download_path": record.get("download_path", ""),
        "active": record.get("active", 1), "created_at": record.get("created_at", ""),
        "updated_at": record.get("updated_at"), "bgm_season": bgm.get("season"),
        "bgm_sort_start": sort_range[0], "bgm_sort_end": sort_range[1],
        "bgm_subject_name": bgm.get("subject_name"),
        "bgm_series_name": bgm.get("series_name"), "bgm_rating": bgm.get("rating"),
        "bgm_air_date": bgm.get("air_date"),
        "tmdb_id": tmdb.get("id"), "tmdb_season": tmdb.get("season"),
        "tmdb_ep_offset": tmdb.get("ep_offset"),
        "tvdb_id": tvdb.get("id"), "tvdb_season": tvdb.get("season"),
        "tvdb_ep_offset": tvdb.get("ep_offset"),
    }
    for key, value in fields.items():
        setattr(row, key, value)
    for kind in ("primary", "backup"):
        data = record.get(kind)
        feed = session.get(SubscriptionFeed, (bangumi_id, kind))
        if data is None:
            if feed:
                session.delete(feed)
        else:
            if feed is None:
                feed = SubscriptionFeed(bangumi_id=bangumi_id, kind=kind)
                session.add(feed)
            feed.rss_url = data.get("rss_url", "")
            feed.subgroup_id = data.get("subgroup_id", 0)
            feed.subgroup_name = data.get("subgroup_name", "")
            feed.offset = data.get("offset")
        session.query(SubscriptionFeedRule).filter_by(bangumi_id=bangumi_id, kind=kind).delete()
        if data is not None:
            for rule_type in ("filter_tags", "exclude_patterns"):
                for index, value in enumerate(data.get(rule_type) or []):
                    session.add(SubscriptionFeedRule(bangumi_id=bangumi_id, kind=kind,
                                                     rule_type=rule_type, position=index,
                                                     value=value))


def ensure_subscriptions(path: Path, migrate):
    with new_session() as session, session.begin():
        if session.get(LegacyImport, SUB_TABLE_IMPORT):
            return
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        session.expire_all()
        if session.get(LegacyImport, SUB_TABLE_IMPORT):
            return
        old_table = session.connection().exec_driver_sql(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='subscriptions'").first()
        records = []
        if old_table:
            records = [json.loads(value) for (value,) in session.connection().exec_driver_sql(
                "SELECT data FROM subscriptions ORDER BY position, bangumi_id")]
        if not records and session.get(LegacyImport, SUB_IMPORT) is None and path.is_file():
            records = _document(path, list)
        migrate(records)
        for position, record in enumerate(records):
            if not isinstance(record, dict) or not isinstance(record.get("bangumi_id"), int):
                raise ValueError(f"Invalid subscription record in {path}")
            if session.get(Subscription, record["bangumi_id"]) is None:
                _write_subscription(session, record, position)
        session.flush()
        if old_table:
            session.connection().exec_driver_sql("DROP TABLE subscriptions")
        if session.get(LegacyImport, SUB_IMPORT) is None:
            session.add(LegacyImport(name=SUB_IMPORT))
        session.add(LegacyImport(name=SUB_TABLE_IMPORT))
        logger.info("Migrated %d subscriptions to relational tables", len(records))


def list_subscriptions(path: Path, migrate) -> list[dict]:
    ensure_subscriptions(path, migrate)
    with new_session() as session:
        return [_subscription_record(session, row) for row in session.scalars(
            select(Subscription).order_by(Subscription.position, Subscription.bangumi_id))]


def mutate_subscription(path: Path, migrate, bangumi_id: int, operation):
    ensure_subscriptions(path, migrate)
    with new_session() as session, session.begin():
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row = session.get(Subscription, bangumi_id)
        before = _subscription_record(session, row) if row else None
        after, result = operation(before)
        if after is None:
            if row:
                structured_values.clear(session, "subscription", bangumi_id)
                session.query(SubscriptionFeedRule).filter_by(bangumi_id=bangumi_id).delete()
                session.query(SubscriptionFeed).filter_by(bangumi_id=bangumi_id).delete()
                session.delete(row)
        else:
            last_position = session.scalar(select(func.max(Subscription.position))) if row is None else None
            position = row.position if row else (last_position if last_position is not None else -1) + 1
            _write_subscription(session, after, position)
        return result


MAPPING_FIELDS = ("name", "name_original", "mikan_id", "anidb_id", "tmdb_id",
                  "tvdb_id", "tmdb_season", "tvdb_season")
MAPPING_TEXT_FIELDS = {"name", "name_original"}


def _mapping_dict(session, row: BangumiMapping) -> dict:
    return {**structured_values.read(session, "mapping", row.bangumi_id, "extras", {}),
            **{field: getattr(row, field) for field in MAPPING_FIELDS
               if getattr(row, field) is not None}}


def _mapping_overrides(session, bangumi_id: int) -> dict:
    return {**structured_values.read(session, "mapping", bangumi_id, "override_extras", {}),
            **{item.field: (item.text_value if item.field in MAPPING_TEXT_FIELDS else item.int_value)
               for item in session.scalars(select(BangumiMappingOverride).where(
                   BangumiMappingOverride.bangumi_id == bangumi_id))}}


def _write_mapping(session, bangumi_id: int, values: dict, overrides: dict) -> None:
    structured_values.replace(session, "mapping", bangumi_id, "extras",
                              {key: value for key, value in values.items()
                               if key not in MAPPING_FIELDS})
    structured_values.replace(session, "mapping", bangumi_id, "override_extras",
                              {key: value for key, value in overrides.items()
                               if key not in MAPPING_FIELDS})
    row = session.get(BangumiMapping, bangumi_id)
    if row is None:
        row = BangumiMapping(bangumi_id=bangumi_id)
        session.add(row)
    for field in MAPPING_FIELDS:
        setattr(row, field, values.get(field))
    session.query(BangumiMappingOverride).filter_by(bangumi_id=bangumi_id).delete()
    for field, value in overrides.items():
        if field not in MAPPING_FIELDS:
            continue
        session.add(BangumiMappingOverride(
            bangumi_id=bangumi_id, field=field,
            text_value=value if field in MAPPING_TEXT_FIELDS else None,
            int_value=value if field not in MAPPING_TEXT_FIELDS else None))


def ensure_mappings(path: Path):
    with new_session() as session, session.begin():
        if session.get(LegacyImport, MAP_TABLE_IMPORT):
            return
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        session.expire_all()
        if session.get(LegacyImport, MAP_TABLE_IMPORT):
            return
        old_table = session.connection().exec_driver_sql(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bangumi_mappings'").first()
        if old_table:
            rows = session.connection().exec_driver_sql(
                "SELECT bangumi_id, data, overrides FROM bangumi_mappings").all()
            records = [(int(bid), json.loads(data), json.loads(overrides))
                       for bid, data, overrides in rows]
        elif session.get(LegacyImport, MAP_IMPORT) is None:
            if not path.is_file():
                raise FileNotFoundError(f"Bangumi-Mikan mapping not found at {path}")
            records = [(int(key), value, {}) for key, value in _document(path, dict).items()]
        else:
            records = []
        for bangumi_id, values, overrides in records:
            if not isinstance(values, dict) or not isinstance(overrides, dict):
                raise ValueError(f"Invalid Bangumi mapping entry: {bangumi_id}")
            if session.get(BangumiMapping, bangumi_id) is None:
                _write_mapping(session, bangumi_id, values, overrides)
        session.flush()
        if old_table:
            session.connection().exec_driver_sql("DROP TABLE bangumi_mappings")
        if session.get(LegacyImport, MAP_IMPORT) is None:
            session.add(LegacyImport(name=MAP_IMPORT))
        session.add(LegacyImport(name=MAP_TABLE_IMPORT))
        logger.info("Migrated %d Bangumi mappings to relational tables", len(records))


def list_mappings(path: Path) -> dict[int, dict]:
    ensure_mappings(path)
    with new_session() as session:
        return {row.bangumi_id: _mapping_dict(session, row) for row in session.scalars(select(BangumiMapping))}


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
        _write_mapping(session, bangumi_id, {**_mapping_dict(session, row), **fields},
                       {**_mapping_overrides(session, bangumi_id), **fields})
        return True


def add_mapping(path: Path, bangumi_id: int, name: str, name_original: str = "") -> None:
    """Persist a Bangumi search selection so the normal Mikan flow can use it."""
    ensure_mappings(path)
    fields = {"name": name, "name_original": name_original}
    with new_session() as session, session.begin():
        if session.get(BangumiMapping, bangumi_id) is None:
            _write_mapping(session, bangumi_id, fields, fields)


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
            overrides = _mapping_overrides(session, bangumi_id) if row else {}
            merged = {**upstream, **overrides}
            _write_mapping(session, bangumi_id, merged, overrides)
        # Retain user-corrected entries removed by upstream, including their original metadata.
        for row in current.values():
            if not _mapping_overrides(session, row.bangumi_id):
                session.query(BangumiMappingOverride).filter_by(bangumi_id=row.bangumi_id).delete()
                structured_values.clear(session, "mapping", row.bangumi_id)
                session.delete(row)
