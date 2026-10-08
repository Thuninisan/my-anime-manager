"""Per-episode download history and one-time import from legacy JSON storage."""

import json
import logging
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert

from .connection import new_session
from .models import DownloadEpisode, LegacyImport
from . import structured_values

logger = logging.getLogger(__name__)
IMPORT_NAME = "download_episodes:v1"
DOCUMENT_IMPORT_NAME = "download_history:relational:v2"
FIELDS = ("rss_url", "guid", "source", "pub_date", "info_hash", "at",
          "tmdb_ep", "tmdb_season", "tvdb_ep", "tmdb_ep_calc", "fail_count")


def _entry(row):
    result = {field: getattr(row, field) for field in FIELDS if field != "fail_count"}
    if row.fail_count:
        result["fail_count"] = row.fail_count
    from ..domain.persistence import history_snapshot
    result["episode_mapping_snapshot"] = history_snapshot(row)
    mapping = result["episode_mapping_snapshot"]["episode_mapping"]
    result["tmdb_ep_calc"] = mapping["tmdb"]["episode_number"]
    result["tvdb_ep"] = mapping["tvdb"]["episode_number"]
    return result


def ensure_imported(path: Path):
    with new_session() as session, session.begin():
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        if session.get(LegacyImport, DOCUMENT_IMPORT_NAME):
            return
        old_table = session.connection().exec_driver_sql(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='json_documents'").first()
        old_raw = session.connection().exec_driver_sql(
            "SELECT data FROM json_documents WHERE name='download_history'").scalar() if old_table else None
        document = (json.loads(old_raw) if old_raw else
                    json.loads(path.read_text(encoding="utf-8")) if
                    session.get(LegacyImport, IMPORT_NAME) is None and path.is_file() else {})
        if not isinstance(document, dict) or not isinstance(document.get("episodes", {}), dict):
            raise ValueError("Invalid legacy download history")
        if old_table:
            other = session.connection().exec_driver_sql(
                "SELECT name FROM json_documents WHERE name != 'download_history'").first()
            if other:
                raise ValueError(f"Unexpected legacy JSON document: {other[0]}")
        structured_values.replace(session, "history", "download_history", "metadata",
                                  {key: value for key, value in document.items() if key != "episodes"})
        count = 0
        if session.get(LegacyImport, IMPORT_NAME) is None:
            for bangumi_key, episodes in document.get("episodes", {}).items():
                if not isinstance(episodes, dict):
                    raise ValueError("Invalid legacy download history episodes")
                for episode_key, entry in episodes.items():
                    if not isinstance(entry, dict):
                        raise ValueError("Invalid legacy download history entry")
                    values = {field: entry[field] for field in FIELDS if field in entry}
                    values["fail_count"] = entry.get("fail_count") or 0
                    values["status"] = "downloaded" if any(entry.get(k) for k in ("source", "at", "guid", "info_hash")) else "failed"
                    session.execute(insert(DownloadEpisode).values(
                        bangumi_id=int(bangumi_key), episode_number=int(episode_key), **values
                    ).on_conflict_do_nothing(index_elements=[DownloadEpisode.bangumi_id, DownloadEpisode.episode_number]))
                    count += 1
            session.add(LegacyImport(name=IMPORT_NAME))
        session.flush()
        if old_table:
            session.connection().exec_driver_sql("DROP TABLE json_documents")
        session.add(LegacyImport(name=DOCUMENT_IMPORT_NAME))
        logger.info("Imported %d download episodes", count)


def get_episode(path: Path, bangumi_id: int, ep_num: int):
    ensure_imported(path)
    with new_session() as session:
        row = session.get(DownloadEpisode, (bangumi_id, ep_num))
        return (_entry(row), row.status) if row else (None, None)


def get_all(path: Path, bangumi_id: int):
    ensure_imported(path)
    with new_session() as session:
        return {str(row.episode_number): _entry(row) for row in session.scalars(
            select(DownloadEpisode).where(DownloadEpisode.bangumi_id == bangumi_id))}


def mutate_episode(path: Path, bangumi_id: int, ep_num: int, operation):
    ensure_imported(path)
    with new_session() as session, session.begin():
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row = session.get(DownloadEpisode, (bangumi_id, ep_num))
        return operation(session, row)


def clear(path: Path, bangumi_id: int):
    ensure_imported(path)
    with new_session() as session, session.begin():
        return session.execute(delete(DownloadEpisode).where(DownloadEpisode.bangumi_id == bangumi_id)).rowcount


def legacy_document(path: Path):
    """Compatibility view for callers of the private _load_hist helper."""
    ensure_imported(path)
    with new_session() as session:
        result = structured_values.read(session, "history", "download_history", "metadata", {})
        episodes = {}
        for row in session.scalars(select(DownloadEpisode)):
            episodes.setdefault(str(row.bangumi_id), {})[str(row.episode_number)] = _entry(row)
        result["episodes"] = episodes
        return result
