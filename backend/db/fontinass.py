"""Subtitle task checkpoints owned by torrent cards."""
from sqlalchemy import delete, select
from .models import TorrentFontinassTask, TorrentFontinassFile

FILE_FIELDS = ("path", "status", "attempts", "input_hash", "output_hash", "code", "messages", "error")


def read(session, card_id):
    task = session.get(TorrentFontinassTask, card_id)
    if task is None:
        return None
    result = {**(task.extra_data or {}), "status": task.status}
    if task.ready is not None:
        result["ready"] = task.ready
    if task.server_url is not None or task.timeout_seconds is not None:
        result["settings"] = {**result.get("settings", {}), "server": task.server_url, "timeout": task.timeout_seconds}
    result["files"] = [{**(row.extra_data or {}), **{key: getattr(row, key) for key in FILE_FIELDS if getattr(row, key) is not None}}
                       for row in session.scalars(select(TorrentFontinassFile).where(
                           TorrentFontinassFile.card_id == card_id).order_by(TorrentFontinassFile.position))]
    return result


def replace(session, card_id, value):
    session.execute(delete(TorrentFontinassFile).where(TorrentFontinassFile.card_id == card_id))
    task = session.get(TorrentFontinassTask, card_id)
    if value is None:
        if task is not None:
            session.delete(task)
        return
    if task is None:
        task = TorrentFontinassTask(card_id=card_id)
        session.add(task)
    task.status = value.get("status", "waiting")
    task.ready = value.get("ready")
    settings = value.get("settings") or {}
    task.server_url = settings.get("server")
    task.timeout_seconds = settings.get("timeout")
    task.extra_data = {key: item for key, item in value.items() if key not in {"status", "ready", "settings", "files"}}
    settings_extra = {key: item for key, item in settings.items() if key not in {"server", "timeout"}}
    if settings_extra:
        task.extra_data = {**task.extra_data, "settings": settings_extra}
    session.flush()
    for position, item in enumerate(value.get("files", [])):
        session.add(TorrentFontinassFile(card_id=card_id, position=position,
                    **{key: item.get(key) for key in FILE_FIELDS if key in item},
                    extra_data={key: val for key, val in item.items() if key not in FILE_FIELDS}))
