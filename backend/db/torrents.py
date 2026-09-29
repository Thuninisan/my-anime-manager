"""Torrent cards and resumable processing plans stored through the ORM."""

import json
import logging
import threading
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert

from ..utils.paths import USER_DATA_DIR
from .connection import new_session
from .models import LegacyImport, TorrentCard

logger = logging.getLogger(__name__)
LEGACY_FILE = USER_DATA_DIR / "torrents.json"
IMPORT_NAME = "torrents.json:v1"
_import_lock = threading.Lock()

CARD_FIELDS = {
    "info_hash", "torrent_name", "show_name", "bgm_rating", "poster_url", "status",
    "created_at", "updated_at", "encoding_group", "video_codec", "bangumi_ids", "processing",
}


def _card_values(record: dict) -> dict:
    return {
        "info_hash": record["info_hash"],
        "torrent_name": record.get("torrent_name") or "",
        "show_name": record.get("show_name") or "",
        "bgm_rating": record.get("bgm_rating") or 0,
        "poster_url": record.get("poster_url") or "",
        "status": record.get("status") or "downloading",
        "created_at": record.get("created_at") or "",
        "updated_at": record.get("updated_at") or "",
        "encoding_group": record.get("encoding_group") or "",
        "video_codec": record.get("video_codec") or "",
        "bangumi_ids": record.get("bangumi_ids") or [],
        "processing": record.get("processing"),
        "extra_data": {key: value for key, value in record.items() if key not in CARD_FIELDS},
    }


def _as_dict(card: TorrentCard) -> dict:
    result = dict(card.extra_data or {})
    for field in CARD_FIELDS - {"processing"}:
        result[field] = getattr(card, field)
    if card.processing is not None:
        result["processing"] = card.processing
    return result


def _ensure_legacy_imported() -> None:
    if not LEGACY_FILE.is_file():
        return
    with _import_lock:
        with new_session() as session:
            if session.get(LegacyImport, IMPORT_NAME):
                return
        document = json.loads(LEGACY_FILE.read_text(encoding="utf-8"))
        records = document.get("torrents") if isinstance(document, dict) else None
        if not isinstance(records, list):
            raise ValueError(f"Invalid legacy torrent document: {LEGACY_FILE}")
        with new_session() as session, session.begin():
            for record in records:
                if not isinstance(record, dict) or not record.get("info_hash"):
                    raise ValueError(f"Invalid legacy torrent record: {LEGACY_FILE}")
                session.execute(insert(TorrentCard).values(**_card_values(record)).on_conflict_do_nothing(
                    index_elements=[TorrentCard.info_hash]
                ))
            session.execute(insert(LegacyImport).values(name=IMPORT_NAME).on_conflict_do_nothing(
                index_elements=[LegacyImport.name]
            ))
        logger.info("Imported %d Torrent cards from %s", len(records), LEGACY_FILE)


def list_torrents() -> list[dict]:
    _ensure_legacy_imported()
    with new_session() as session:
        cards = session.scalars(select(TorrentCard).order_by(TorrentCard.id)).all()
        return [_as_dict(card) for card in cards]


def save_torrent(record: dict) -> None:
    """Upsert a card by info hash, preserving its first creation time."""
    _ensure_legacy_imported()
    with new_session() as session, session.begin():
        card = session.scalar(select(TorrentCard).where(TorrentCard.info_hash == record["info_hash"]))
        if card is None:
            session.add(TorrentCard(**_card_values(record)))
            return
        record["created_at"] = card.created_at
        for key, value in _card_values(record).items():
            setattr(card, key, value)


def list_pending_torrents() -> list[dict]:
    _ensure_legacy_imported()
    with new_session() as session:
        cards = session.scalars(select(TorrentCard).where(
            TorrentCard.status == "downloading", TorrentCard.processing.is_not(None)
        ).order_by(TorrentCard.id)).all()
        return [_as_dict(card) for card in cards]


def finish_torrent(info_hash: str, status: str) -> None:
    _ensure_legacy_imported()
    with new_session() as session, session.begin():
        card = session.scalar(select(TorrentCard).where(TorrentCard.info_hash == info_hash))
        if card is None:
            return
        card.status = status
        card.updated_at = datetime.now().astimezone().isoformat(timespec="seconds")
        card.processing = None
