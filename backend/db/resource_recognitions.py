"""Replace derived title and candidate data without touching collected resources."""

from sqlalchemy import delete, select

from .connection import new_session
from .models import Resource, ResourceBangumiCandidate
from .resources import _as_dict


def save(resource_id: int, snapshot: dict, status: str, candidates: list[dict], error: str = "") -> None:
    with new_session() as session, session.begin():
        record = session.get(Resource, resource_id)
        if record is None:
            raise ValueError(f"Resource {resource_id} does not exist")
        record.recognition_title_snapshot = snapshot
        record.recognition_status = status
        record.recognition_error = error
        session.execute(delete(ResourceBangumiCandidate).where(
            ResourceBangumiCandidate.resource_id == resource_id))
        for candidate in candidates:
            session.add(ResourceBangumiCandidate(resource_id=resource_id, **candidate))


def get(resource_id: int) -> dict | None:
    with new_session() as session:
        record = session.get(Resource, resource_id)
        if record is None or record.recognition_status is None:
            return None
        candidates = session.scalars(select(ResourceBangumiCandidate).where(
            ResourceBangumiCandidate.resource_id == resource_id)).all()
        from ..domain.resource_adapters import monitor_resource_candidates
        resource = record
        candidate_rows = [{column.key: getattr(item, column.key)
                           for column in ResourceBangumiCandidate.__table__.columns}
                          for item in candidates]
        return {"resource_candidates": monitor_resource_candidates(
                    candidate_rows, resource.index_type, title=resource.title) if resource else [],
                "title_snapshot": record.recognition_title_snapshot or {},
                "status": record.recognition_status,
                "error": record.recognition_error,
                "candidates": [{column.key: getattr(item, column.key)
                                for column in ResourceBangumiCandidate.__table__.columns}
                               for item in candidates]}


def list_unrecognized_resources() -> list[dict]:
    """Collected resources with no recognition attempt yet."""
    with new_session() as session:
        rows = session.scalars(
            select(Resource)
            .where(Resource.status == "complete", Resource.recognition_status.is_(None))
            .order_by(Resource.id)
        ).all()
        return [_as_dict(row) for row in rows]


def list_bangumi_resources() -> list[dict]:
    """Group recognized resources by Bangumi ID, once per torrent."""
    from .. import data

    with new_session() as session:
        rows = session.execute(
            select(ResourceBangumiCandidate.bangumi_id, Resource)
            .join(Resource, Resource.id == ResourceBangumiCandidate.resource_id)
            .order_by(ResourceBangumiCandidate.bangumi_id, Resource.id.desc())
        ).all()
        snapshots = {resource.id: resource.recognition_title_snapshot or {}
            for _, resource in rows if resource.recognition_status is not None}

    grouped: dict[int, dict] = {}
    seen: set[tuple[int, int]] = set()
    for bangumi_id, resource in rows:
        key = (bangumi_id, resource.id)
        if key in seen:
            continue
        seen.add(key)
        entry = grouped.setdefault(bangumi_id, {
            "bangumi_id": bangumi_id,
            "name": (data.get_map_entry(bangumi_id) or {}).get("title_cn") or resource.title,
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
