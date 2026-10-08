"""Preview persistence only. JSON ownership and validation live in the service."""
from sqlalchemy import delete as sql_delete, update as sql_update
from .connection import new_session
from .models import TorrentPreviewSession


def create(row: TorrentPreviewSession) -> None:
    with new_session() as session, session.begin():
        session.add(row)


def get(preview_id: str) -> TorrentPreviewSession | None:
    with new_session() as session:
        return session.get(TorrentPreviewSession, preview_id)


def update(preview_id: str, expected_revision: int, **values: str | int) -> bool:
    with new_session() as session, session.begin():
        result = session.execute(sql_update(TorrentPreviewSession).where(
            TorrentPreviewSession.id == preview_id,
            TorrentPreviewSession.revision == expected_revision,
        ).values(**values))
        return result.rowcount == 1


def delete(preview_id: str) -> None:
    with new_session() as session, session.begin():
        row = session.get(TorrentPreviewSession, preview_id)
        if row is not None:
            session.delete(row)


def cleanup_expired(now: str) -> list[str]:
    with new_session() as session, session.begin():
        return list(session.scalars(sql_delete(TorrentPreviewSession).where(
            TorrentPreviewSession.expires_at <= now).returning(TorrentPreviewSession.id)))
