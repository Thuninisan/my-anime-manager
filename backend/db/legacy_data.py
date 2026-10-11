"""Relational SQLite storage for subscriptions and Bangumi mappings."""

from sqlalchemy import select, func

from .connection import new_session
from .models import (BangumiMapping, Subscription,
                     SubscriptionFeed)

def _subscription_record(session, row: Subscription) -> dict:
    from .identity import read_resource_identity
    identity = read_resource_identity(row)
    record = {"bangumi_id": row.bangumi_id, "name": row.name,
              "download_path": row.download_path, "active": row.active,
              "created_at": row.created_at}
    record.update(row.extra_data or {})
    if row.poster_url is not None:
        record["poster_url"] = row.poster_url
    if row.updated_at is not None:
        record["updated_at"] = row.updated_at
    if row.series_name is not None:
        record["series_name"] = row.series_name
    bgm_extras = row.bgm_extra_data or {}
    if bgm_extras or any(value is not None for value in (
            row.bgm_season, row.bgm_sort_start, row.bgm_sort_end,
            row.bgm_subject_name, row.bgm_series_name, row.bgm_rating, row.bgm_air_date)):
        record["bgm"] = {"season": row.bgm_season if row.bgm_season is not None else 1,
                         "sortrange": [row.bgm_sort_start or 0, row.bgm_sort_end or 0],
                         "subject_name": row.bgm_subject_name or "",
                         "series_name": row.bgm_series_name or "",
                         "rating": row.bgm_rating or 0, "air_date": row.bgm_air_date or ""}
        record["bgm"].update(bgm_extras)
    for kind in ("tmdb", "tvdb"):
        season = getattr(row, f"{kind}_season")
        source_id = identity["tmdb_movie_id"] if kind == "tmdb" and identity["media_type"] == "movie" else identity[f"{kind}_series_id"]
        offset = getattr(row, f"{kind}_ep_offset")
        extras = getattr(row, f"{kind}_extra_data") or {}
        if extras or any(value is not None for value in (season, source_id, offset)):
            record[kind] = {"season": season, "ep_offset": offset or 0}
            record[kind].update(extras)
    record["resource_identity"] = identity
    record["identity_revision"] = row.identity_revision or 1
    record["identity_source"] = row.identity_source or "legacy_unknown"
    for kind in ("primary", "backup"):
        feed = session.get(SubscriptionFeed, (row.bangumi_id, kind))
        if feed is None:
            continue
        value = {"rss_url": feed.rss_url, "subgroup_id": feed.subgroup_id,
                 "subgroup_name": feed.subgroup_name}
        if feed.offset is not None:
            value["offset"] = feed.offset
        for rule_type in ("filter_tags", "exclude_patterns"):
            value[rule_type] = list(getattr(feed, rule_type))
        value.update(feed.extra_data or {})
        record[kind] = value
    return record


def _write_subscription(session, record: dict, position: int, *, identity_source="explicit_user_mapping") -> None:
    fields_by_group = {
        "record": {"bangumi_id", "name", "series_name", "download_path", "active",
                   "created_at", "updated_at", "bgm", "tmdb", "tvdb", "primary", "backup",
                   "resource_identity", "resource_resolution", "identity_source", "identity_revision", "poster_url"},
        "bgm": {"season", "sortrange", "subject_name", "series_name", "rating", "air_date"},
        "tmdb": {"id", "season", "ep_offset"},
        "tvdb": {"id", "season", "ep_offset"},
        "primary": {"rss_url", "subgroup_id", "subgroup_name", "offset",
                    "filter_tags", "exclude_patterns"},
        "backup": {"rss_url", "subgroup_id", "subgroup_name", "offset",
                   "filter_tags", "exclude_patterns"},
    }
    bangumi_id = record["bangumi_id"]
    row = session.get(Subscription, bangumi_id)
    if row is None:
        row = Subscription(bangumi_id=bangumi_id, position=position)
        session.add(row)
    row.extra_data = {key: value for key, value in record.items() if key not in fields_by_group["record"]}
    row.poster_url = record.get("poster_url")
    for group in ("bgm", "tmdb", "tvdb"):
        setattr(row, f"{group}_extra_data", {key: value for key, value in (record.get(group) or {}).items() if key not in fields_by_group[group]})
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
        "tmdb_season": tmdb.get("season"),
        "tmdb_ep_offset": tmdb.get("ep_offset"),
        "tvdb_season": tvdb.get("season"),
        "tvdb_ep_offset": tvdb.get("ep_offset"),
    }
    from .identity import update_resource_identity, read_resource_identity
    from ..domain.resource_adapters import provider_binding_identity
    current = read_resource_identity(row) if row.resource_identity_json is not None else None
    requested = record.get("resource_identity")
    # Provider IDs in input are explicit binding edits, not read-side mirrors.
    binding_edited = any("id" in settings for settings in (tmdb, tvdb))
    if requested is None or binding_edited:
        base = requested or current or {}
        media_type = base.get("media_type", "tv")
        requested = provider_binding_identity(
            title=record.get("series_name"), bangumi_id=bangumi_id, media_type=media_type,
            tmdb_id=tmdb.get("id", base.get("tmdb_movie_id") if media_type == "movie" else base.get("tmdb_series_id")),
            tvdb_id=tvdb.get("id", base.get("tvdb_series_id")))
    else:
        requested = {**requested, "canonical_title": record.get("series_name")}
    for key, value in fields.items():
        setattr(row, key, value)
    update_resource_identity(row, requested, source=identity_source)
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
            feed.extra_data = {key: value for key, value in data.items() if key not in fields_by_group[kind]}
            feed.rss_url = data.get("rss_url", "")
            feed.subgroup_id = data.get("subgroup_id", 0)
            feed.subgroup_name = data.get("subgroup_name", "")
            feed.offset = data.get("offset")
            feed.filter_tags = list(data.get("filter_tags") or [])
            feed.exclude_patterns = list(data.get("exclude_patterns") or [])


def list_subscriptions() -> list[dict]:
    with new_session() as session:
        return [_subscription_record(session, row) for row in session.scalars(
            select(Subscription).order_by(Subscription.position, Subscription.bangumi_id))]


def mutate_subscription(bangumi_id: int, operation):
    with new_session() as session, session.begin():
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row = session.get(Subscription, bangumi_id)
        before = _subscription_record(session, row) if row else None
        after, result = operation(before)
        if after is None:
            if row:
                session.query(SubscriptionFeed).filter_by(bangumi_id=bangumi_id).delete()
                session.delete(row)
        else:
            last_position = session.scalar(select(func.max(Subscription.position))) if row is None else None
            position = row.position if row else (last_position if last_position is not None else -1) + 1
            _write_subscription(session, after, position,
                                identity_source=after.pop("_identity_update_source", "legacy_unknown"))
        return result


MAPPING_FIELDS = ("title_cn", "title_jp", "mikan_id", "tmdb_id",
                  "tvdb_id", "tmdb_season", "tvdb_season")


def _mapping_dict(session, row: BangumiMapping) -> dict:
    return {field: getattr(row, field) for field in (*MAPPING_FIELDS, "override")
            if getattr(row, field) is not None}


def _write_mapping(session, bangumi_id: int, values: dict, *, override: bool) -> None:
    row = session.get(BangumiMapping, bangumi_id)
    if row is None:
        row = BangumiMapping(bangumi_id=bangumi_id)
        session.add(row)
    for field in MAPPING_FIELDS:
        setattr(row, field, values.get(field))
    if row.tmdb_season is None:
        row.tmdb_season = 1
    row.override = override


def list_mappings() -> dict[int, dict]:
    with new_session() as session:
        return {row.bangumi_id: _mapping_dict(session, row) for row in session.scalars(select(BangumiMapping))}


def mapping_count() -> int:
    with new_session() as session:
        return session.scalar(select(func.count()).select_from(BangumiMapping)) or 0


def set_mapping_fields(bangumi_id: int, fields: dict, *, manual: bool = True) -> bool:
    with new_session() as session, session.begin():
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row = session.get(BangumiMapping, bangumi_id)
        if row is None:
            return False
        if not manual and row.override:
            return False
        if not fields or any(field not in MAPPING_FIELDS for field in fields):
            raise ValueError("Invalid mapping fields")
        _write_mapping(session, bangumi_id, {**_mapping_dict(session, row), **fields}, override=manual or row.override)
        return True


def add_mapping(bangumi_id: int, name: str, name_original: str = "") -> None:
    """Persist a Bangumi search selection so the normal Mikan flow can use it."""
    fields = {"title_cn": name, "title_jp": name_original}
    with new_session() as session, session.begin():
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        if session.get(BangumiMapping, bangumi_id) is None:
            _write_mapping(session, bangumi_id, fields, override=True)


def replace_mappings(records: dict, *, preserve_fields=()):
    """Replace upstream dataset atomically, preserving manually edited rows."""
    if not isinstance(records, dict) or not records:
        raise ValueError("Empty or invalid Bangumi mapping dataset")
    normalized = {}
    for key, value in records.items():
        if not isinstance(value, dict) or not isinstance(value.get("title_cn"), str):
            raise ValueError(f"Invalid Bangumi mapping entry: {key}")
        normalized[int(key)] = value
    with new_session() as session, session.begin():
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        current = {row.bangumi_id: row for row in session.scalars(select(BangumiMapping))}
        for bangumi_id, upstream in normalized.items():
            row = current.pop(bangumi_id, None)
            if row is not None and row.override:
                continue
            preserved = {field: getattr(row, field) for field in preserve_fields} if row else {}
            merged = {**upstream, **preserved}
            _write_mapping(session, bangumi_id, merged, override=False)
        # Retain user-corrected entries removed by upstream, including their original metadata.
        for row in current.values():
            if not row.override:
                session.delete(row)
