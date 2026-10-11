"""RSS download history with relational episode mappings."""

from sqlalchemy import delete, select

from .connection import new_session
from .models import DownloadEpisode, DownloadEpisodeMapping
from .download_episode_mapping import as_dict

FIELDS = ("guid", "source", "pub_date", "info_hash", "at", "download_path", "fail_count")


def _entry(row, mapping):
    result = {field: getattr(row, field) for field in FIELDS if field != "fail_count"}
    if row.fail_count:
        result["fail_count"] = row.fail_count
    result["mapping"] = as_dict(mapping)
    return result


def get_episode(bangumi_id: int, ep_num: int):
    with new_session() as session:
        row = session.get(DownloadEpisode, (bangumi_id, ep_num))
        mapping = session.get(DownloadEpisodeMapping, (bangumi_id, ep_num)) if row else None
        return (_entry(row, mapping), row.status) if row else (None, None)


def _joined_rows(session, bangumi_id=None):
    query = select(DownloadEpisode, DownloadEpisodeMapping).outerjoin(
        DownloadEpisodeMapping,
        (DownloadEpisodeMapping.bangumi_id == DownloadEpisode.bangumi_id) &
        (DownloadEpisodeMapping.episode_number == DownloadEpisode.episode_number))
    if bangumi_id is not None:
        query = query.where(DownloadEpisode.bangumi_id == bangumi_id)
    return session.execute(query.order_by(DownloadEpisode.bangumi_id, DownloadEpisode.episode_number))


def get_all(bangumi_id: int):
    with new_session() as session:
        return {str(row.episode_number): _entry(row, mapping)
                for row, mapping in _joined_rows(session, bangumi_id)}


def mutate_episode(bangumi_id: int, ep_num: int, operation):
    with new_session() as session, session.begin():
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row = session.get(DownloadEpisode, (bangumi_id, ep_num))
        return operation(session, row)


def clear(bangumi_id: int):
    with new_session() as session, session.begin():
        return session.execute(delete(DownloadEpisode).where(DownloadEpisode.bangumi_id == bangumi_id)).rowcount


def history_document():
    """Compatibility view for callers of the private _load_hist helper."""
    with new_session() as session:
        episodes = {}
        for row, mapping in _joined_rows(session):
            episodes.setdefault(str(row.bangumi_id), {})[str(row.episode_number)] = _entry(row, mapping)
        return {"episodes": episodes}
