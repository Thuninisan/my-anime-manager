"""Torrent cards and resumable processing plans stored through the ORM."""

import json
from datetime import datetime

from sqlalchemy import delete, select

from .connection import new_session
from .models import TorrentCard, TorrentCardBangumi, TorrentCardOperation
from . import fontinass


CARD_FIELDS = {
    "info_hash", "torrent_name", "show_name", "bgm_rating", "poster_url", "status",
    "created_at", "updated_at", "encoding_group", "video_codec", "bangumi_ids", "processing", "completion_snapshot",
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
    result = dict(card.extra_data or {})
    subtitle_state = fontinass.read(session, card.id)
    if subtitle_state is not None:
        result["fontinass"] = subtitle_state
    for field in CARD_FIELDS - {"processing", "completion_snapshot"}:
        if field == "bangumi_ids":
            result[field] = [row.bangumi_id for row in session.scalars(
                select(TorrentCardBangumi).where(TorrentCardBangumi.card_id == card.id)
                .order_by(TorrentCardBangumi.position))]
        else:
            result[field] = getattr(card, field)
    if card.completion_snapshot_json is not None:
        if card.completion_schema_version != 1:
            raise ValueError("unsupported_torrent_completion_version")
        result["completion_snapshot"] = json.loads(card.completion_snapshot_json)
    if card.processing_present:
        processing = dict(card.processing_extra_data or {})
        if card.replaced_history_snapshot is not None:
            processing["replaced_history"] = card.replaced_history_snapshot
        if card.processing_mode is not None:
            processing["mode"] = card.processing_mode
        if card.replace_bangumi_id is not None:
            processing["replace_bangumi_id"] = card.replace_bangumi_id
        processing["files"] = [
            {**(row.extra_data or {}),
             **{field: getattr(row, field) for field in (
                 "torrent_path", "source_path", "target_path", "action", "bangumi_sort", "episode_mapping_snapshot", "resource_identity")
                if getattr(row, field) is not None}}
            for row in session.scalars(select(TorrentCardOperation).where(
                TorrentCardOperation.card_id == card.id).order_by(TorrentCardOperation.position))]
        if processing.get("replace_bangumi_id") is not None:
            from ..legacy.torrent import replacement_operation_view
            processing["files"] = [replacement_operation_view(op, processing["replace_bangumi_id"]) for op in processing["files"]]
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
    for position, operation in enumerate((processing or {}).get("files", [])):
        save_operation(session, card.id, position, operation)
    card.replaced_history_snapshot = (processing or {}).get("replaced_history")
    card.processing_extra_data = {key: value for key, value in (processing or {}).items()
                                 if key not in {"mode", "replace_bangumi_id", "files", "replaced_history"}}
    card.extra_data = {key: value for key, value in record.items()
                      if key not in CARD_FIELDS and key != "fontinass"}
    fontinass.replace(session, card.id, record.get("fontinass"))
    return card


def list_torrents() -> list[dict]:
    with new_session() as session:
        cards = session.scalars(select(TorrentCard).order_by(TorrentCard.id)).all()
        return [_as_dict(session, card) for card in cards]


def save_torrent(record: dict) -> None:
    """Upsert a card by info hash, preserving its first creation time."""
    with new_session() as session, session.begin():
        _save_card(session, record)


def list_pending_torrents() -> list[dict]:
    with new_session() as session:
        cards = session.scalars(select(TorrentCard).where(
            TorrentCard.status == "downloading", TorrentCard.processing_present == 1
        ).order_by(TorrentCard.id)).all()
        return [_as_dict(session, card) for card in cards]


def get_torrent(info_hash: str) -> dict | None:
    with new_session() as session:
        card = session.scalar(select(TorrentCard).where(TorrentCard.info_hash == info_hash))
        return _as_dict(session, card) if card else None


def save_fontinass(info_hash: str, value: dict) -> None:
    """Checkpoint subtitle processing without replacing the media processing plan."""
    with new_session() as session, session.begin():
        card = session.scalar(select(TorrentCard).where(TorrentCard.info_hash == info_hash))
        if card is None:
            return
        fontinass.replace(session, card.id, value)
        card.updated_at = datetime.now().astimezone().isoformat(timespec="seconds")


def finish_torrent(info_hash: str, status: str) -> None:
    with new_session() as session, session.begin():
        card = session.scalar(select(TorrentCard).where(TorrentCard.info_hash == info_hash))
        if card is None:
            return
        if status == "completed" and card.processing_present:
            from ..domain.persistence import load_episode_mapping_snapshot
            plan = _as_dict(session, card)["processing"]
            operations = plan["files"]
            for operation in operations:
                if operation.get("episode_mapping_snapshot") is not None:
                    operation["episode_mapping_snapshot"] = load_episode_mapping_snapshot(operation["episode_mapping_snapshot"])
                if plan.get("replace_bangumi_id") is not None and operation.get("action") == "hardlink":
                    operation["target_path"] = operation.get("target_path", "").removesuffix(".mam-bd-pending")
            card.completion_snapshot_json = json.dumps({"schema_version": 1, "files": operations,
                                                       "replaced_history": plan.get("replaced_history")},
                                                       ensure_ascii=False, allow_nan=False)
            card.completion_schema_version = 1
        card.status = status
        card.updated_at = datetime.now().astimezone().isoformat(timespec="seconds")
        card.processing_present = 0
        card.processing_mode = None
        card.replace_bangumi_id = None
        session.execute(delete(TorrentCardOperation).where(TorrentCardOperation.card_id == card.id))
        card.processing_extra_data = {}
        card.replaced_history_snapshot = None


def save_operation(session, card_id, position, operation):
    fields = {"torrent_path", "source_path", "target_path", "action", "bangumi_sort",
              "episode_mapping_snapshot", "resource_identity"}
    session.add(TorrentCardOperation(card_id=card_id, position=position,
                **{key: operation.get(key) for key in fields},
                extra_data={key: value for key, value in operation.items() if key not in fields}))
