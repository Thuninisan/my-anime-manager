"""Torrent cards and resumable processing plans stored through the ORM."""

import json
import logging
import threading
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert

from ..utils.paths import USER_DATA_DIR
from .connection import new_session
from .models import LegacyImport, StructuredNode, TorrentCard, TorrentCardBangumi, TorrentCardOperation
from . import structured_values

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
    }


def _as_dict(session, card: TorrentCard) -> dict:
    result = structured_values.read(session, "torrent_card", card.id, "extra_data", {})
    for field in CARD_FIELDS - {"processing"}:
        if field == "bangumi_ids":
            result[field] = [row.bangumi_id for row in session.scalars(
                select(TorrentCardBangumi).where(TorrentCardBangumi.card_id == card.id)
                .order_by(TorrentCardBangumi.position))]
        else:
            result[field] = getattr(card, field)
    if card.processing_present:
        processing = structured_values.read(session, "torrent_card", card.id,
                                            "processing_extra", {})
        if card.processing_mode is not None:
            processing["mode"] = card.processing_mode
        if card.replace_bangumi_id is not None:
            processing["replace_bangumi_id"] = card.replace_bangumi_id
        processing["files"] = [
            {**structured_values.read(session, "torrent_card", card.id,
                                      f"operation:{row.position}", {}),
             **{field: getattr(row, field) for field in (
                 "torrent_path", "source_path", "target_path", "action", "bangumi_sort")
                if getattr(row, field) is not None}}
            for row in session.scalars(select(TorrentCardOperation).where(
                TorrentCardOperation.card_id == card.id).order_by(TorrentCardOperation.position))]
        result["processing"] = processing
    return result


def _save_card(session, record: dict) -> TorrentCard:
    card = session.scalar(select(TorrentCard).where(TorrentCard.info_hash == record["info_hash"]))
    if card is None:
        card = TorrentCard(**_card_values(record))
        session.add(card)
        session.flush()
    else:
        record["created_at"] = card.created_at
        for key, value in _card_values(record).items():
            setattr(card, key, value)
    session.execute(delete(TorrentCardBangumi).where(TorrentCardBangumi.card_id == card.id))
    for position, bangumi_id in enumerate(record.get("bangumi_ids") or []):
        session.add(TorrentCardBangumi(card_id=card.id, position=position,
                                       bangumi_id=bangumi_id))
    processing = record.get("processing")
    card.processing_present = int(processing is not None)
    card.processing_mode = processing.get("mode") if processing else None
    card.replace_bangumi_id = processing.get("replace_bangumi_id") if processing else None
    session.execute(delete(TorrentCardOperation).where(TorrentCardOperation.card_id == card.id))
    session.execute(delete(StructuredNode).where(
        StructuredNode.owner_kind == "torrent_card", StructuredNode.owner_id == str(card.id),
        StructuredNode.root_field.like("operation:%")))
    for position, operation in enumerate((processing or {}).get("files", [])):
        session.add(TorrentCardOperation(card_id=card.id, position=position,
                                         **{key: operation.get(key) for key in (
                                             "torrent_path", "source_path", "target_path",
                                             "action", "bangumi_sort")}))
        structured_values.replace(session, "torrent_card", card.id, f"operation:{position}",
                                  {key: value for key, value in operation.items()
                                   if key not in {"torrent_path", "source_path", "target_path",
                                                  "action", "bangumi_sort"}})
    structured_values.replace(session, "torrent_card", card.id, "processing_extra",
                              {key: value for key, value in (processing or {}).items()
                               if key not in {"mode", "replace_bangumi_id", "files"}})
    structured_values.replace(session, "torrent_card", card.id, "extra_data",
                              {key: value for key, value in record.items()
                               if key not in CARD_FIELDS})
    return card


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
                if session.scalar(select(TorrentCard).where(
                        TorrentCard.info_hash == record["info_hash"])) is None:
                    _save_card(session, record)
            session.execute(insert(LegacyImport).values(name=IMPORT_NAME).on_conflict_do_nothing(
                index_elements=[LegacyImport.name]
            ))
        logger.info("Imported %d Torrent cards from %s", len(records), LEGACY_FILE)


def list_torrents() -> list[dict]:
    _ensure_legacy_imported()
    with new_session() as session:
        cards = session.scalars(select(TorrentCard).order_by(TorrentCard.id)).all()
        return [_as_dict(session, card) for card in cards]


def save_torrent(record: dict) -> None:
    """Upsert a card by info hash, preserving its first creation time."""
    _ensure_legacy_imported()
    with new_session() as session, session.begin():
        _save_card(session, record)


def list_pending_torrents() -> list[dict]:
    _ensure_legacy_imported()
    with new_session() as session:
        cards = session.scalars(select(TorrentCard).where(
            TorrentCard.status == "downloading", TorrentCard.processing_present == 1
        ).order_by(TorrentCard.id)).all()
        return [_as_dict(session, card) for card in cards]


def finish_torrent(info_hash: str, status: str) -> None:
    _ensure_legacy_imported()
    with new_session() as session, session.begin():
        card = session.scalar(select(TorrentCard).where(TorrentCard.info_hash == info_hash))
        if card is None:
            return
        card.status = status
        card.updated_at = datetime.now().astimezone().isoformat(timespec="seconds")
        card.processing_present = 0
        card.processing_mode = None
        card.replace_bangumi_id = None
        session.execute(delete(TorrentCardOperation).where(TorrentCardOperation.card_id == card.id))
        session.execute(delete(StructuredNode).where(
            StructuredNode.owner_kind == "torrent_card", StructuredNode.owner_id == str(card.id),
            StructuredNode.root_field.like("operation:%")))
        structured_values.replace(session, "torrent_card", card.id, "processing_extra", {})
