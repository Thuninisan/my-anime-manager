"""Per-episode download history and one-time import from legacy JSON storage."""

import json
import logging
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert

from .connection import new_session
from .models import DownloadEpisode, JsonDocument, LegacyImport

logger = logging.getLogger(__name__)
IMPORT_NAME = "download_episodes:v1"
FIELDS = ("rss_url", "guid", "source", "pub_date", "info_hash", "at",
          "tmdb_ep", "tmdb_season", "tvdb_ep", "tmdb_ep_calc", "fail_count")


def _entry(row):
    result = {field: getattr(row, field) for field in FIELDS if field != "fail_count"}
    if row.fail_count:
        result["fail_count"] = row.fail_count
    return result


def ensure_imported(path: Path):
    with new_session() as session, session.begin():
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        if session.get(LegacyImport, IMPORT_NAME):
            return
        old = session.get(JsonDocument, "download_history")
        document = old.data if old else json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        if not isinstance(document, dict) or not isinstance(document.get("episodes", {}), dict):
            raise ValueError("Invalid legacy download history")
        if old is None and document:
            session.add(JsonDocument(name="download_history", data=document))
        count = 0
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
        old = session.get(JsonDocument, "download_history")
        result = {k: v for k, v in (old.data if old else {}).items() if k != "episodes"}
        episodes = {}
        for row in session.scalars(select(DownloadEpisode)):
            episodes.setdefault(str(row.bangumi_id), {})[str(row.episode_number)] = _entry(row)
        result["episodes"] = episodes
        return result
