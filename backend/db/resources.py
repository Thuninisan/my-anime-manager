"""Resource repository backed by SQLAlchemy ORM sessions."""

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import case, delete, func, or_, select
from sqlalchemy.dialects.sqlite import insert

from ..utils.paths import USER_DATA_DIR
from .connection import new_session
from .models import Resource, ResourceTorrentFile, StructuredNode
from . import structured_values

TORRENT_ROOT = USER_DATA_DIR / "resource_monitor" / "torrents"
FEED_FIELDS = ("source", "source_id", "index_type", "title", "published_at", "detail_url", "torrent_url",
               "rss_description", "info_hash", "size_label")
UPDATE_FIELDS = {"detail_description", "detail_fetched", "info_hash", "torrent_path",
                 "torrent_name", "torrent_files", "status", "error"}


def _as_dict(record: Resource, session) -> dict:
    result = {column.key: getattr(record, column.key) for column in Resource.__table__.columns}
    result["torrent_files"] = [{**structured_values.read(
        session, "resource", record.id, f"torrent_file:{row.position}", {}),
        "name": row.name} for row in session.scalars(
        select(ResourceTorrentFile).where(ResourceTorrentFile.resource_id == record.id)
        .order_by(ResourceTorrentFile.position))]
    return result


def upsert_feed_item(item: dict) -> dict:
    values = {field: item.get(field, "tmdb") if field == "index_type" else item[field]
              for field in FEED_FIELDS}
    with new_session() as session, session.begin():
        statement = insert(Resource).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[Resource.source, Resource.source_id],
            set_={
                "title": statement.excluded.title,
                "published_at": statement.excluded.published_at,
                "detail_url": statement.excluded.detail_url,
                "torrent_url": statement.excluded.torrent_url,
                "rss_description": statement.excluded.rss_description,
                "size_label": statement.excluded.size_label,
                "info_hash": case((Resource.info_hash == "", statement.excluded.info_hash),
                                  else_=Resource.info_hash),
                "updated_at": func.current_timestamp(),
            },
        )
        session.execute(statement)
        record = session.scalar(select(Resource).where(
            Resource.source == item["source"], Resource.source_id == item["source_id"]
        ))
        assert record is not None
        return _as_dict(record, session)


def update_resource(resource_id: int, **changes) -> None:
    if not changes or not set(changes) <= UPDATE_FIELDS:
        raise ValueError("Invalid resource update")
    with new_session() as session, session.begin():
        record = session.get(Resource, resource_id)
        if record is None:
            return
        files = changes.pop("torrent_files", None)
        if files is not None:
            session.execute(delete(ResourceTorrentFile).where(ResourceTorrentFile.resource_id == resource_id))
            session.execute(delete(StructuredNode).where(
                StructuredNode.owner_kind == "resource", StructuredNode.owner_id == str(resource_id),
                StructuredNode.root_field.like("torrent_file:%")))
            for position, item in enumerate(files):
                session.add(ResourceTorrentFile(resource_id=resource_id, position=position,
                                                name=item["name"]))
                structured_values.replace(session, "resource", resource_id,
                                          f"torrent_file:{position}",
                                          {key: value for key, value in item.items() if key != "name"})
        for key, value in changes.items():
            setattr(record, key, value)
        record.updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def get_resource(resource_id: int) -> dict | None:
    with new_session() as session:
        record = session.get(Resource, resource_id)
        return _as_dict(record, session) if record else None


def list_resources(q: str = "", source: str = "", status: str = "",
                   limit: int = 50, offset: int = 0) -> dict:
    filters = []
    if q:
        filters.append(or_(Resource.title.contains(q, autoescape=True),
                           Resource.detail_description.contains(q, autoescape=True)))
    if source:
        filters.append(Resource.source == source)
    if status:
        filters.append(Resource.status == status)
    with new_session() as session:
        total = session.scalar(select(func.count()).select_from(Resource).where(*filters)) or 0
        records = session.scalars(select(Resource).where(*filters).order_by(
            Resource.published_at.desc(), Resource.id.desc()
        ).limit(limit).offset(offset)).all()
        return {"total": total, "items": [_as_dict(record, session) for record in records]}


def torrent_file_path(source: str, source_id: str) -> Path:
    digest = hashlib.sha256(source_id.encode("utf-8")).hexdigest()
    return TORRENT_ROOT / source / f"{digest}.torrent"
