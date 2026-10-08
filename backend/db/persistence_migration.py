"""Additive, repeatable SQLite upgrade; deterministic explicit backfill."""
import json
from sqlalchemy import select

IDENTITY_COLUMNS = {"resource_identity_json": "TEXT", "identity_source": "TEXT",
                    "identity_updated_at": "TEXT", "identity_revision": "INTEGER",
                    "identity_schema_version": "INTEGER"}
HISTORY_COLUMNS = {"episode_mapping_snapshot": "TEXT", "episode_mapping_schema_version": "INTEGER"}


def upgrade(connection):
    for table, additions in (("rss_subscriptions", IDENTITY_COLUMNS),
                             ("download_episodes", HISTORY_COLUMNS),
                             ("torrent_cards", {"completion_snapshot_json": "TEXT", "completion_schema_version": "INTEGER"})):
        columns = {row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({table})")}
        for name, kind in additions.items():
            if name not in columns:
                connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")


def backfill(session):
    from .models import Subscription, DownloadEpisode
    from .identity import read_resource_identity, update_resource_identity
    from ..legacy.history import legacy_history_to_episode_mapping
    from ..domain.persistence import EPISODE_MAPPING_SNAPSHOT_VERSION
    stats = {"identities": 0, "history_partial": 0, "skipped_canonical": 0, "unresolved": 0}
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
    for row in session.scalars(select(DownloadEpisode)):
        if row.episode_mapping_snapshot is not None:
            stats["skipped_canonical"] += 1
            continue
        if row.status != "downloaded":
            stats["unresolved"] += 1
            continue
        snapshot = legacy_history_to_episode_mapping(row)
        if snapshot["episode_mapping"]["bangumi"]["subject_id"] is None:
            stats["unresolved"] += 1
            continue
        row.episode_mapping_snapshot = json.dumps(snapshot, ensure_ascii=False, allow_nan=False)
        row.episode_mapping_schema_version = EPISODE_MAPPING_SNAPSHOT_VERSION
        stats["history_partial"] += 1
    return stats
