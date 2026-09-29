"""Replace derived title and candidate data without touching collected resources."""

from sqlalchemy import delete, select

from .connection import new_session
from .models import Resource, ResourceRecognition, ResourceBangumiCandidate
from .resources import _as_dict
from . import structured_values


def save(resource_id: int, snapshot: dict, status: str, candidates: list[dict], error: str = "") -> None:
    with new_session() as session, session.begin():
        if session.get(Resource, resource_id) is None:
            raise ValueError(f"Resource {resource_id} does not exist")
        record = session.get(ResourceRecognition, resource_id)
        if record is None:
            record = ResourceRecognition(resource_id=resource_id)
            session.add(record)
        structured_values.replace(session, "recognition", resource_id, "title_snapshot", snapshot)
        record.status = status
        record.error = error
        session.execute(delete(ResourceBangumiCandidate).where(
            ResourceBangumiCandidate.resource_id == resource_id))
        for candidate in candidates:
            session.add(ResourceBangumiCandidate(resource_id=resource_id, **candidate))


def get(resource_id: int) -> dict | None:
    with new_session() as session:
        record = session.get(ResourceRecognition, resource_id)
        if record is None:
            return None
        candidates = session.scalars(select(ResourceBangumiCandidate).where(
            ResourceBangumiCandidate.resource_id == resource_id)).all()
        return {"title_snapshot": structured_values.read(
                    session, "recognition", resource_id, "title_snapshot", {}),
                "status": record.status,
                "error": record.error,
                "candidates": [{column.key: getattr(item, column.key)
                                for column in ResourceBangumiCandidate.__table__.columns}
                               for item in candidates]}


def list_unrecognized_resources() -> list[dict]:
    """Collected resources with no recognition attempt yet."""
    with new_session() as session:
        rows = session.scalars(
            select(Resource).outerjoin(ResourceRecognition,
                                       ResourceRecognition.resource_id == Resource.id)
            .where(Resource.status == "complete", ResourceRecognition.resource_id.is_(None))
            .order_by(Resource.id)
        ).all()
        return [_as_dict(row, session) for row in rows]


def list_bangumi_resources() -> list[dict]:
    """Group recognized resources by Bangumi ID, once per torrent."""
    from .. import data

    with new_session() as session:
        rows = session.execute(
            select(ResourceBangumiCandidate.bangumi_id, Resource,
                   ResourceRecognition)
            .join(Resource, Resource.id == ResourceBangumiCandidate.resource_id)
            .outerjoin(ResourceRecognition,
                       ResourceRecognition.resource_id == Resource.id)
            .order_by(ResourceBangumiCandidate.bangumi_id, Resource.id.desc())
        ).all()
        snapshots = {resource.id: structured_values.read(
            session, "recognition", resource.id, "title_snapshot", {})
            for _, resource, recognition in rows if recognition}

    grouped: dict[int, dict] = {}
    seen: set[tuple[int, int]] = set()
    for bangumi_id, resource, recognition in rows:
        key = (bangumi_id, resource.id)
        if key in seen:
            continue
        seen.add(key)
        entry = grouped.setdefault(bangumi_id, {
            "bangumi_id": bangumi_id,
            "name": (data.get_map_entry(bangumi_id) or {}).get("name") or resource.title,
            "torrents": [],
        })
        entry["torrents"].append({
            "resource_id": resource.id,
            "name": resource.torrent_name or resource.title,
            "video_codec": snapshots.get(resource.id, {}).get("video_codec") or "",
            "source": resource.source,
            "published_at": resource.published_at,
        })
    return list(grouped.values())
