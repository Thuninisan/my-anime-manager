"""Torrent business records; unfinished plans live in shared recovery storage."""

import json
import logging
from datetime import datetime

from sqlalchemy import delete, or_, select

from .connection import new_session
from .models import TorrentCard, WorkflowRecovery
from . import recovery

logger = logging.getLogger(__name__)

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
    for field in CARD_FIELDS - {"processing", "completion_snapshot"}:
        if field == "bangumi_ids":
            result[field] = list(card.bangumi or [])
        else:
            result[field] = getattr(card, field)
    if card.completion_snapshot_json is not None:
        if card.completion_schema_version != 1:
            raise ValueError("unsupported_torrent_completion_version")
        result["completion_snapshot"] = json.loads(card.completion_snapshot_json)
    job = recovery.read(session, "torrent_processing", card.info_hash)
    if job is not None:
        result["processing"] = job.checkpoint["processing"]
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
    card.bangumi = list(record.get("bangumi_ids") or [])
    processing = record.get("processing")
    if processing is not None:
        recovery.create(session, "torrent_processing", card.info_hash, "wait_download",
                        {"processing": processing})
    card.extra_data = {key: value for key, value in record.items()
                      if key not in CARD_FIELDS}
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
    with new_session() as session, session.begin():
        keys = recovery.pending("torrent_processing")
        cards = session.scalars(select(TorrentCard).where(TorrentCard.info_hash.in_(keys))).all()
        orphaned = set(keys) - {card.info_hash for card in cards}
        for key in orphaned:
            removed = session.execute(delete(WorkflowRecovery).where(
                WorkflowRecovery.workflow_type == "torrent_processing",
                WorkflowRecovery.business_key == key,
                ~select(TorrentCard.id).where(TorrentCard.info_hash == key).exists(),
                or_(WorkflowRecovery.lease_owner.is_(None),
                    WorkflowRecovery.lease_expires_at <= recovery.now())))
            if removed.rowcount:
                logger.warning("workflow.recovery.orphan torrent=%s reason=business_record_missing", key)
        return [_as_dict(session, card) for card in cards]


def finish_torrent(info_hash: str, status: str, *, job: recovery.RecoveryJob,
                   keep_recovery: bool = False) -> None:
    """Commit business completion and remove its recovery checkpoint atomically."""
    with new_session() as session, session.begin():
        job.guard(session)
        card = session.scalar(select(TorrentCard).where(TorrentCard.info_hash == info_hash))
        if card is None:
            job.delete(session)
            return
        if status == "completed":
            from ..domain.persistence import load_episode_mapping_snapshot
            from copy import deepcopy
            plan = deepcopy(job.checkpoint["processing"])
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
        if not keep_recovery:
            job.delete(session)
