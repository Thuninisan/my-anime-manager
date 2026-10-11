"""Transactional, repeatable SQLite upgrades and explicit backfill."""
import json
import logging
from sqlalchemy import select

from .mapping_fields import LOCAL_FIELDS, merge_mapping

logger = logging.getLogger(__name__)

IDENTITY_COLUMNS = {"resource_identity_json": "TEXT", "identity_source": "TEXT",
                    "identity_updated_at": "TEXT", "identity_revision": "INTEGER",
                    "identity_schema_version": "INTEGER"}


def upgrade(connection):
    _upgrade_mappings(connection)
    for table, additions in (("rss_subscriptions", IDENTITY_COLUMNS),
                             ("torrent_cards", {"completion_snapshot_json": "TEXT", "completion_schema_version": "INTEGER"})):
        columns = {row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({table})")}
        for name, kind in additions.items():
            if name not in columns:
                connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")

    _upgrade_torrent_cards(connection)
    columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(rss_subscriptions)")}
    if "mapping_migrated" not in columns:
        connection.exec_driver_sql("ALTER TABLE rss_subscriptions ADD COLUMN mapping_migrated BOOLEAN NOT NULL DEFAULT 0")


def _upgrade_torrent_cards(connection):
    """Fold ordered card associations into JSON and remove obsolete tables."""
    columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(torrent_cards)")}
    if "bangumi" not in columns:
        connection.exec_driver_sql("ALTER TABLE torrent_cards ADD COLUMN bangumi JSON NOT NULL DEFAULT '[]'")
    old_table = connection.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='torrent_card_bangumi'").first()
    if old_table:
        associations = {}
        for card_id, bangumi_id in connection.exec_driver_sql(
                "SELECT card_id, bangumi_id FROM torrent_card_bangumi ORDER BY card_id, position"):
            associations.setdefault(card_id, []).append(bangumi_id)
        for card_id, ids in associations.items():
            row = connection.exec_driver_sql("SELECT bangumi FROM torrent_cards WHERE id=?", (card_id,)).first()
            if row is None:
                raise ValueError(f"Missing torrent card for Bangumi association: {card_id}")
            current = json.loads(row[0])
            if current and current != ids:
                raise ValueError(f"Conflicting torrent card Bangumi associations: {card_id}")
            connection.exec_driver_sql("UPDATE torrent_cards SET bangumi=? WHERE id=?", (json.dumps(ids), card_id))
        connection.exec_driver_sql("DROP TABLE torrent_card_bangumi")
    connection.exec_driver_sql("DROP TABLE IF EXISTS torrent_card_operations")


def _upgrade_mappings(connection):
    """Fold legacy per-field overrides into the mapping and protect edited rows."""
    columns = {row[1] for row in connection.exec_driver_sql(
        "PRAGMA table_info(bangumi_mappings_v2)")}
    for old, new in (("name", "title_cn"), ("name_original", "title_jp")):
        if old in columns and new not in columns:
            connection.exec_driver_sql(f"ALTER TABLE bangumi_mappings_v2 RENAME COLUMN {old} TO {new}")
    if "override" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE bangumi_mappings_v2 ADD COLUMN override BOOLEAN NOT NULL DEFAULT 0")
    old_table = connection.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bangumi_mapping_overrides'").first()
    if old_table:
        fields = {"name": "title_cn", "name_original": "title_jp",
                  "title_cn": "title_cn", "title_jp": "title_jp",
                  "mikan_id": "mikan_id", "tmdb_id": "tmdb_id", "tmdb_season": "tmdb_season",
                  "tvdb_id": "tvdb_id", "tvdb_season": "tvdb_season"}
        for bangumi_id, field, text_value, int_value in connection.exec_driver_sql(
                "SELECT bangumi_id, field, text_value, int_value FROM bangumi_mapping_overrides").all():
            target = fields.get(field)
            if target is None:
                continue
            value = text_value if target in {"title_cn", "title_jp"} else int_value
            if target == "tmdb_season" and value is None:
                value = 1
            connection.exec_driver_sql(
                f"UPDATE bangumi_mappings_v2 SET {target}=?, override=1 WHERE bangumi_id=?",
                (value, bangumi_id))
        connection.exec_driver_sql("DROP TABLE bangumi_mapping_overrides")
    if "anidb_id" in columns:
        connection.exec_driver_sql("ALTER TABLE bangumi_mappings_v2 DROP COLUMN anidb_id")
    connection.exec_driver_sql("UPDATE bangumi_mappings_v2 SET tmdb_season=1 WHERE tmdb_season IS NULL")
    # Rebuild old nullable season definitions to give direct inserts the same default.
    info = {row[1]: row for row in connection.exec_driver_sql("PRAGMA table_info(bangumi_mappings_v2)")}
    if not info["tmdb_season"][3] or str(info["tmdb_season"][4]) != "1":
        local_definitions = ", ".join(f"{field} {'INTEGER' if field.endswith('_ep_offset') else 'TEXT'}"
                                      for field in LOCAL_FIELDS)
        connection.exec_driver_sql(f"""CREATE TABLE bangumi_mappings_upgrade (
            bangumi_id INTEGER NOT NULL PRIMARY KEY, title_cn TEXT, title_jp TEXT,
            mikan_id INTEGER, tmdb_id INTEGER, tvdb_id INTEGER,
            tmdb_season INTEGER NOT NULL DEFAULT 1, tvdb_season INTEGER,
            override BOOLEAN NOT NULL DEFAULT 0, {local_definitions})""")
        fields = "bangumi_id,title_cn,title_jp,mikan_id,tmdb_id,tvdb_id,tmdb_season,tvdb_season,override"
        fields += "".join(f",{field}" for field in LOCAL_FIELDS if field in info)
        connection.exec_driver_sql(f"INSERT INTO bangumi_mappings_upgrade ({fields}) SELECT {fields} FROM bangumi_mappings_v2")
        connection.exec_driver_sql("DROP TABLE bangumi_mappings_v2")
        connection.exec_driver_sql("ALTER TABLE bangumi_mappings_upgrade RENAME TO bangumi_mappings_v2")
        connection.exec_driver_sql("CREATE INDEX idx_bangumi_mappings_tmdb ON bangumi_mappings_v2 (tmdb_id)")
        connection.exec_driver_sql("CREATE INDEX idx_bangumi_mappings_tvdb ON bangumi_mappings_v2 (tvdb_id)")
    # Add after legacy table rebuilding, otherwise the old rebuild loses new fields.
    columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(bangumi_mappings_v2)")}
    for field in LOCAL_FIELDS:
        if field not in columns:
            kind = "INTEGER" if field.endswith("_ep_offset") else "TEXT"
            connection.exec_driver_sql(f"ALTER TABLE bangumi_mappings_v2 ADD COLUMN {field} {kind}")


def migrate_subscription_mappings(connection):
    """Backfill once, after legacy extension migration; preserve confirmed bindings."""
    from sqlalchemy.orm import Session
    from .models import Subscription, BangumiMapping
    from .identity import read_resource_identity
    from .mapping_fields import MAPPING_FIELDS
    with Session(bind=connection) as session:
        for sub in session.scalars(select(Subscription).where(Subscription.mapping_migrated.is_(False))):
            identity = read_resource_identity(sub)
            # Movie identity needs a separate typed mapping; do not treat movie IDs as series IDs.
            if identity["media_type"] != "tv":
                continue
            row = session.get(BangumiMapping, sub.bangumi_id)
            current = {field: getattr(row, field) for field in MAPPING_FIELDS} if row else {}
            changes = {}
            for provider in ("tmdb", "tvdb"):
                provider_id = identity[f"{provider}_series_id"]
                if provider_id is None:
                    continue
                changes[f"{provider}_id"] = provider_id
                for suffix in ("season", "ep_offset"):
                    value = getattr(sub, f"{provider}_{suffix}")
                    if value is not None:
                        changes[f"{provider}_{suffix}"] = value
            conflicts = [field for field, value in changes.items()
                         if row and row.override and current.get(field) is not None
                         and current[field] != value]
            if conflicts:
                logger.warning("订阅映射迁移保留旧配置: bangumi_id=%s override 冲突字段=%s",
                               sub.bangumi_id, conflicts)
                continue
            binding_changed = any(current.get(field) != value for field, value in changes.items()
                                  if field.endswith("_id") or field.endswith("_season"))
            if row is None:
                row = BangumiMapping(bangumi_id=sub.bangumi_id,
                                     title_cn=sub.name or str(sub.bangumi_id))
                session.add(row)
            merged = merge_mapping(current, changes)
            for field, value in merged.items():
                setattr(row, field, value)
            if row.tmdb_season is None:
                row.tmdb_season = 1
            # Protect a confirmed subscription binding that differs from community data.
            if binding_changed:
                row.override = True
            for provider in ("tmdb", "tvdb"):
                setattr(sub, f"{provider}_season", None)
                setattr(sub, f"{provider}_ep_offset", None)
                extras = dict(getattr(sub, f"{provider}_extra_data") or {})
                for field in ("id", "season", "ep_offset"):
                    extras.pop(field, None)
                setattr(sub, f"{provider}_extra_data", extras)
            # Old subject_name mixes languages. Keep it only as an unmigrated display fallback.
            sub.mapping_migrated = True
        session.flush()


def backfill(session):
    from .models import Subscription
    from .identity import read_resource_identity, update_resource_identity
    stats = {"identities": 0, "skipped_canonical": 0, "unresolved": 0}
    for row in session.scalars(select(Subscription)):
        if row.resource_identity_json is not None:
            stats["skipped_canonical"] += 1
            continue
        try:
            identity = read_resource_identity(row)
            update_resource_identity(row, identity, source="legacy_backfill")
            stats["identities"] += 1
        except (ValueError, TypeError):
            stats["unresolved"] += 1
    return stats
