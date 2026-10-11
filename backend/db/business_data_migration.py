"""Move legacy extension trees into their owning business tables atomically."""
import json
from collections import defaultdict
from sqlalchemy.orm import Session
from .models import (Subscription, SubscriptionFeed, Resource, ResourceSource,
                     TorrentCard)

ADDITIONS = {
    "rss_subscriptions": {"poster_url": "TEXT", **{key: "JSON NOT NULL DEFAULT '{}'" for key in
        ("extra_data", "bgm_extra_data", "tmdb_extra_data", "tvdb_extra_data")}},
    "rss_subscription_feeds": {"extra_data": "JSON NOT NULL DEFAULT '{}'"},
    "resources": {"recognition_title_snapshot": "JSON", "recognition_status": "TEXT",
                  "recognition_error": "TEXT NOT NULL DEFAULT ''"},
    "resource_sources": {"download_tag_extra_data": "JSON NOT NULL DEFAULT '{}'"},
    "torrent_cards": {"extra_data": "JSON NOT NULL DEFAULT '{}'"},
}


def add_columns(connection):
    connection.exec_driver_sql("DROP TABLE IF EXISTS download_history_metadata")
    for table, additions in ADDITIONS.items():
        columns = {row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({table})")}
        for name, definition in additions.items():
            if name not in columns:
                connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _merge(row, field, value):
    current = getattr(row, field) or {}
    conflicts = {key for key in current.keys() & value.keys() if current[key] != value[key]}
    if conflicts:
        raise ValueError(f"Conflicting extension values: {field}, {sorted(conflicts)}")
    setattr(row, field, {**current, **value})


def assign(session, kind, owner_id, field, value):
    if not isinstance(value, dict):
        raise ValueError(f"Expected extension object: {kind}/{owner_id}/{field}")
    value = dict(value)
    # Earlier releases attached title snapshots to a separate recognition row.
    if kind == "recognition" and field == "title_snapshot":
        kind, field = "resource", "recognition_title_snapshot"
    model = {"subscription": Subscription, "resource": Resource, "resource_source": ResourceSource,
             "torrent_card": TorrentCard}.get(kind)
    row = session.get(model, owner_id if kind == "resource_source" else int(owner_id)) if model else None
    if row is None:
        raise ValueError(f"Missing extension owner: {kind}/{owner_id}/{field}")
    if kind == "subscription":
        if field == "extras":
            if "poster_url" in value:
                poster = value.pop("poster_url")
                if row.poster_url is not None and row.poster_url != poster:
                    raise ValueError("Conflicting subscription poster")
                row.poster_url = poster
            for key, feed_kind in (("exclude_patterns", "primary"), ("backup_exclude_patterns", "backup")):
                feed = session.get(SubscriptionFeed, (row.bangumi_id, feed_kind))
                if key in value and feed is not None:
                    rules = value.pop(key)
                    if not isinstance(rules, list) or not all(isinstance(rule, str) for rule in rules):
                        raise ValueError("Invalid legacy exclusion rules")
                    feed.exclude_patterns = list(dict.fromkeys([*(feed.exclude_patterns or []), *rules]))
            _merge(row, "extra_data", value)
            return
        if field in {"bgm_extras", "tmdb_extras", "tvdb_extras"}:
            _merge(row, field.replace("_extras", "_extra_data"), value)
            return
        if field in {"primary_extras", "backup_extras"}:
            feed = session.get(SubscriptionFeed, (row.bangumi_id, field.split("_")[0]))
            if feed is None:
                if value:
                    raise ValueError("Missing subscription feed for extension")
            else:
                _merge(feed, "extra_data", value)
            return
    if kind == "resource" and field == "recognition_title_snapshot":
        if row.recognition_title_snapshot is not None and row.recognition_title_snapshot != value:
            raise ValueError("Conflicting recognition snapshot")
        row.recognition_title_snapshot = value
        return
    if kind == "resource_source" and field == "downloadtag_extra":
        _merge(row, "download_tag_extra_data", value)
        return
    if kind == "torrent_card" and field == "extra_data":
        _merge(row, "extra_data", value)
        return
    raise ValueError(f"Unknown extension field: {kind}/{owner_id}/{field}")


def migrate_nodes(connection):
    legacy_recognitions = connection.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='resource_recognitions'").first()
    if legacy_recognitions:
        connection.exec_driver_sql("""UPDATE resources SET
            recognition_status=(SELECT status FROM resource_recognitions WHERE resource_id=resources.id),
            recognition_error=COALESCE((SELECT error FROM resource_recognitions WHERE resource_id=resources.id), '')
            WHERE recognition_status IS NULL AND id IN (SELECT resource_id FROM resource_recognitions)""")
    exists = connection.exec_driver_sql("SELECT 1 FROM sqlite_master WHERE type='table' AND name='structured_nodes'").first()
    if not exists:
        return
    groups = defaultdict(list)
    for row in connection.exec_driver_sql("SELECT * FROM structured_nodes ORDER BY id").mappings():
        groups[(row["owner_kind"], row["owner_id"], row["root_field"])].append(dict(row))
    with Session(bind=connection) as session:
        for (kind, owner_id, field), nodes in groups.items():
            roots = [node for node in nodes if node["parent_id"] is None]
            if len(roots) != 1:
                raise ValueError("Invalid extension tree roots")
            children = defaultdict(list)
            for node in nodes:
                children[node["parent_id"]].append(node)
            visited = set()
            def build(node):
                if node["id"] in visited:
                    raise ValueError("Cycle in extension tree")
                visited.add(node["id"])
                typ, raw = node["value_type"], node["value_text"]
                items = children[node["id"]]
                if typ == "dict":
                    keys = [item["key_name"] for item in items]
                    if None in keys or len(keys) != len(set(keys)):
                        raise ValueError("Invalid extension dictionary keys")
                    return {item["key_name"]: build(item) for item in items}
                if typ == "list":
                    positions = [item["position"] for item in items]
                    if sorted(positions) != list(range(len(items))):
                        raise ValueError("Invalid extension list positions")
                    return [build(item) for item in sorted(items, key=lambda item: item["position"])]
                if items:
                    raise ValueError("Scalar extension node has children")
                if typ == "null": return None
                if typ == "bool":
                    if raw not in {"0", "1"}: raise ValueError("Invalid extension boolean")
                    return raw == "1"
                if typ == "int": return int(raw)
                if typ == "float": return float(raw)
                if typ == "str": return raw
                raise ValueError(f"Unknown extension type: {typ}")
            value = build(roots[0])
            if len(visited) != len(nodes):
                raise ValueError("Disconnected extension tree")
            assign(session, kind, owner_id, field, value)
        session.flush()
    connection.exec_driver_sql("DROP TABLE structured_nodes")
