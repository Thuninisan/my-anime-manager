"""RSS download history and episode coordinates stored in one table."""

from sqlalchemy import delete, select

from .connection import new_session
from .models import DownloadEpisode
from .download_episode_mapping import as_dict

FIELDS = ("guid", "source", "pub_date", "info_hash", "at", "download_path", "fail_count")


def _entry(row):
    result = {field: getattr(row, field) for field in FIELDS if field != "fail_count"}
    if row.fail_count:
        result["fail_count"] = row.fail_count
    result["mapping"] = as_dict(row)
    return result


def get_episode(bangumi_id: int, ep_num: int):
    with new_session() as session:
        row = session.get(DownloadEpisode, (bangumi_id, ep_num))
        return (_entry(row), row.status) if row else (None, None)


def _rows(session, bangumi_id=None):
    query = select(DownloadEpisode)
    if bangumi_id is not None:
        query = query.where(DownloadEpisode.bangumi_id == bangumi_id)
    return session.scalars(query.order_by(DownloadEpisode.bangumi_id, DownloadEpisode.episode_number))


def get_all(bangumi_id: int):
    with new_session() as session:
        return {str(row.episode_number): _entry(row)
                for row in _rows(session, bangumi_id)}


def mutate_episode(bangumi_id: int, ep_num: int, operation):
    with new_session() as session, session.begin():
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row = session.get(DownloadEpisode, (bangumi_id, ep_num))
        return operation(session, row)


def clear(bangumi_id: int):
    with new_session() as session, session.begin():
        from .mapping_changes import ensure_idle_in_session
        ensure_idle_in_session(session, bangumi_id)
        return session.execute(delete(DownloadEpisode).where(DownloadEpisode.bangumi_id == bangumi_id)).rowcount
