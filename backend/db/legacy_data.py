"""Relational SQLite storage for subscriptions and Bangumi mappings."""

from sqlalchemy import select, func

from .connection import new_session
from .models import (BangumiMapping, Subscription,
                     SubscriptionFeed)
from .mapping_fields import COMMUNITY_FIELDS, LOCAL_FIELDS, MAPPING_FIELDS, merge_mapping

def _subscription_record(session, row: Subscription) -> dict:
    from .identity import read_resource_identity
    identity = read_resource_identity(row)
    mapping = session.get(BangumiMapping, row.bangumi_id)
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
    if mapping:
        title = mapping.bangumi_subject_title_cn or mapping.bangumi_subject_title_jp
        if title:
            record.setdefault("bgm", {})["subject_name"] = title
        record.setdefault("bgm", {}).update(
            subject_title_cn=mapping.bangumi_subject_title_cn,
            subject_title_jp=mapping.bangumi_subject_title_jp)
    for kind in ("tmdb", "tvdb"):
        source_id = identity["tmdb_movie_id"] if kind == "tmdb" and identity["media_type"] == "movie" else identity[f"{kind}_series_id"]
        matches = (identity["media_type"] == "tv" and source_id is not None and mapping is not None
                   and source_id == getattr(mapping, f"{kind}_id"))
        # Protected migration conflicts and movie identities still own their old settings.
        legacy = not row.mapping_migrated
        season = getattr(mapping, f"{kind}_season") if matches and not legacy else (
            getattr(row, f"{kind}_season") if legacy else None)
        offset = getattr(mapping, f"{kind}_ep_offset") if matches and not legacy else (
            getattr(row, f"{kind}_ep_offset") if legacy else None)
        extras = getattr(row, f"{kind}_extra_data") or {}
        record[kind] = {**extras, "season": season, "ep_offset": offset,
                        "series_title_cn": getattr(mapping, f"{kind}_series_title_cn") if matches else None,
                        "series_title_jp": getattr(mapping, f"{kind}_series_title_jp") if matches else None}
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
                   "resource_identity", "resource_resolution", "identity_source", "identity_revision", "poster_url",
                   "_mapping_fields", "_mapping_expected", "_identity_expected"},
        "bgm": {"season", "sortrange", "subject_name", "series_name", "rating", "air_date", "subject_title_cn", "subject_title_jp"},
        "tmdb": {"id", "season", "ep_offset", "series_title_cn", "series_title_jp"},
        "tvdb": {"id", "season", "ep_offset", "series_title_cn", "series_title_jp"},
        "primary": {"rss_url", "subgroup_id", "subgroup_name", "offset",
                    "filter_tags", "exclude_patterns"},
        "backup": {"rss_url", "subgroup_id", "subgroup_name", "offset",
                   "filter_tags", "exclude_patterns"},
    }
    bangumi_id = record["bangumi_id"]
    row = session.get(Subscription, bangumi_id)
    if row is None:
        row = Subscription(bangumi_id=bangumi_id, position=position, mapping_migrated=True)
        session.add(row)
    row.extra_data = {key: value for key, value in record.items() if key not in fields_by_group["record"]}
    row.poster_url = record.get("poster_url")
    for group in ("bgm", "tmdb", "tvdb"):
        setattr(row, f"{group}_extra_data", {key: value for key, value in (record.get(group) or {}).items() if key not in fields_by_group[group]})
    bgm = record.get("bgm") or {}
    sort_range = list(bgm.get("sortrange") or []) + [None, None]
    tmdb = record.get("tmdb") or {}
    tvdb = record.get("tvdb") or {}
    if not row.mapping_migrated:
        # Until a protected conflict/movie row is reconciled, retain its legacy numbering.
        for provider, settings in (("tmdb", tmdb), ("tvdb", tvdb)):
            for suffix in ("season", "ep_offset"):
                setattr(row, f"{provider}_{suffix}", settings.get(suffix))
    fields = {
        "name": record.get("name", ""), "series_name": record.get("series_name"),
        "download_path": record.get("download_path", ""),
        "active": record.get("active", 1), "created_at": record.get("created_at", ""),
        "updated_at": record.get("updated_at"), "bgm_season": bgm.get("season"),
        "bgm_sort_start": sort_range[0], "bgm_sort_end": sort_range[1],
        "bgm_series_name": bgm.get("series_name"), "bgm_rating": bgm.get("rating"),
        "bgm_air_date": bgm.get("air_date"),
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


def mutate_subscription(bangumi_id: int, operation, *, mapping_fields=None, mapping_manual=False,
                        mapping_expected=None, identity_expected=None):
    with new_session() as session, session.begin():
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row = session.get(Subscription, bangumi_id)
        if row and identity_expected is not None and (row.identity_revision or 1) != identity_expected:
            raise ValueError("Subscription identity changed during enrichment; retry")
        before = _subscription_record(session, row) if row else None
        after, result = operation(before)
        if after is None:
            if row:
                session.query(SubscriptionFeed).filter_by(bangumi_id=bangumi_id).delete()
                session.delete(row)
        else:
            last_position = session.scalar(select(func.max(Subscription.position))) if row is None else None
            position = row.position if row else (last_position if last_position is not None else -1) + 1
            if mapping_fields:
                from .identity import read_resource_identity
                if row and read_resource_identity(row)["media_type"] != "tv":
                    raise ValueError("Series mapping edits require a TV subscription")
                if mapping_expected is not None:
                    mapping = session.get(BangumiMapping, bangumi_id)
                    current = _mapping_dict(session, mapping) if mapping else {}
                    if any(current.get(field) != value for field, value in mapping_expected.items()):
                        raise ValueError("Mapping changed during enrichment; retry")
                # An explicit reconciliation resolves a previously protected migration conflict.
                if row and mapping_manual and not row.mapping_migrated:
                    # Copy the other legacy provider too; don't discard its numbering when
                    # resolving one field of a previously protected subscription mapping.
                    from .identity import read_resource_identity
                    identity = read_resource_identity(row)
                    legacy_fields = {}
                    for provider in ("tmdb", "tvdb"):
                        provider_id = identity[f"{provider}_series_id"]
                        if provider_id is None:
                            continue
                        legacy_fields[f"{provider}_id"] = provider_id
                        id_edited = f"{provider}_id" in mapping_fields and mapping_fields[f"{provider}_id"] != provider_id
                        for suffix in ("season", "ep_offset"):
                            if id_edited or (suffix == "ep_offset" and f"{provider}_season" in mapping_fields):
                                continue
                            value = getattr(row, f"{provider}_{suffix}")
                            if value is not None:
                                legacy_fields[f"{provider}_{suffix}"] = value
                    mapping_fields = {**legacy_fields, **mapping_fields}
                    row.mapping_migrated = True
                    for provider in ("tmdb", "tvdb"):
                        setattr(row, f"{provider}_season", None)
                        setattr(row, f"{provider}_ep_offset", None)
                _patch_mapping(session, bangumi_id, mapping_fields, manual=mapping_manual, create=True)
            _write_subscription(session, after, position,
                                identity_source=after.pop("_identity_update_source", "legacy_unknown"))
            if result is after:
                session.flush()
                result = _subscription_record(session, session.get(Subscription, bangumi_id))
        return result


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


def _patch_mapping(session, bangumi_id, fields, *, manual, create=False):
    if not fields or any(field not in MAPPING_FIELDS for field in fields):
        raise ValueError("Invalid mapping fields")
    for field, value in fields.items():
        integer = field.endswith("_id") or field.endswith("_season") or field.endswith("_ep_offset")
        if value is not None and (type(value) is not int if integer else not isinstance(value, str)):
            raise ValueError(f"Invalid mapping value: {field}")
        if value is not None and field.endswith("_id") and value <= 0:
            raise ValueError(f"Invalid mapping ID: {field}")
        if value is not None and field == "tmdb_season" and value < 0:
            raise ValueError("Invalid TMDB season")
    row = session.get(BangumiMapping, bangumi_id)
    if row is None and not create:
        return False
    current = _mapping_dict(session, row) if row else {}
    if row is None:
        current["title_cn"] = fields.get("bangumi_subject_title_cn") or fields.get("bangumi_subject_title_jp") or str(bangumi_id)
    if row and row.override and not manual:
        # Metadata may fill a protected binding, but must not replace its ID/season or known offset.
        for field, value in fields.items():
            if field in COMMUNITY_FIELDS and current.get(field) != value:
                provider = next((p for p in ("tmdb", "tvdb") if field in (f"{p}_id", f"{p}_season")), None)
                if current.get(field) is not None and (provider is None or current.get(f"{provider}_id") is not None):
                    raise ValueError(f"Protected mapping conflict: {field}")
        fields = {field: value for field, value in fields.items()
                  if field not in LOCAL_FIELDS or current.get(field) is None}
    _write_mapping(session, bangumi_id, merge_mapping(current, fields),
                   override=manual or bool(row and row.override))
    return True


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
        _patch_mapping(session, bangumi_id, fields, manual=manual)
        return True


def edit_mapping(bangumi_id: int, fields: dict) -> bool:
    """Explicit mapping edits also update an active subscription's confirmed binding."""
    from .identity import read_resource_identity, update_resource_identity
    from ..domain.resource_adapters import provider_binding_identity
    with new_session() as session, session.begin():
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        mapping = session.get(BangumiMapping, bangumi_id)
        if mapping is None:
            return False
        sub = session.get(Subscription, bangumi_id)
        identity = read_resource_identity(sub) if sub else None
        if identity and identity["media_type"] != "tv" and any(
                field.startswith(("tmdb_", "tvdb_")) for field in fields):
            raise ValueError("Series mapping edits require a TV subscription")
        _patch_mapping(session, bangumi_id, fields, manual=True)
        if identity and identity["media_type"] == "tv":
            requested = provider_binding_identity(
                title=sub.series_name, bangumi_id=bangumi_id,
                tmdb_id=mapping.tmdb_id if "tmdb_id" in fields else identity["tmdb_series_id"],
                tvdb_id=mapping.tvdb_id if "tvdb_id" in fields else identity["tvdb_series_id"])
            update_resource_identity(sub, requested, source="explicit_user_mapping")
            if not sub.mapping_migrated:
                # Compatibility rows retain their own other provider until the conflict is resolved.
                for provider in ("tmdb", "tvdb"):
                    if f"{provider}_id" in fields:
                        setattr(sub, f"{provider}_season", getattr(mapping, f"{provider}_season"))
                        setattr(sub, f"{provider}_ep_offset", getattr(mapping, f"{provider}_ep_offset"))
                    for suffix in ("season", "ep_offset"):
                        if f"{provider}_{suffix}" in fields:
                            setattr(sub, f"{provider}_{suffix}", getattr(mapping, f"{provider}_{suffix}"))
                    if f"{provider}_season" in fields:
                        setattr(sub, f"{provider}_ep_offset", getattr(mapping, f"{provider}_ep_offset"))
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
        subscribed_ids = set(session.scalars(select(Subscription.bangumi_id)))
        for bangumi_id, upstream in normalized.items():
            row = current.pop(bangumi_id, None)
            if row is not None and row.override:
                continue
            preserved = {field: getattr(row, field) for field in preserve_fields} if row else {}
            current_values = _mapping_dict(session, row) if row else {}
            updates = {field: upstream.get(field) for field in COMMUNITY_FIELDS}
            for provider in ("tmdb", "tvdb"):
                # Missing upstream IDs are not a confirmed unbinding. Keep locally enriched
                # bindings (including their season) rather than losing inferred IDs daily.
                has_metadata = any(current_values.get(field) is not None for field in (
                    f"{provider}_series_title_cn", f"{provider}_series_title_jp", f"{provider}_ep_offset"))
                if updates[f"{provider}_id"] is None and row and (has_metadata or bangumi_id in subscribed_ids):
                    for suffix in ("id", "season"):
                        preserved[f"{provider}_{suffix}"] = current_values.get(f"{provider}_{suffix}")
            # Upstream owns only community fields; local metadata survives daily synchronization.
            merged = merge_mapping(current_values, {**updates, **preserved})
            _write_mapping(session, bangumi_id, merged, override=False)
        # Retain user-corrected entries removed by upstream, including their original metadata.
        for row in current.values():
            has_local_data = any(getattr(row, field) is not None for field in LOCAL_FIELDS)
            if not row.override and not has_local_data and row.bangumi_id not in subscribed_ids:
                session.delete(row)
