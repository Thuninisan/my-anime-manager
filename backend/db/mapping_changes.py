"""映射变更的持久化凭证、互斥检查和事务提交。"""

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import delete, select

from .connection import new_session
from .models import BangumiMapping, DownloadEpisode, MappingChangeTask, Subscription, SubscriptionFeed
from .legacy_data import _mapping_dict, _patch_mapping, _subscription_record
from .download_history import _entry
from .download_episode_mapping import save as save_episode_mapping


class MappingConflict(ValueError):
    pass


def snapshot(session, bangumi_id):
    mapping = session.get(BangumiMapping, bangumi_id)
    sub = session.get(Subscription, bangumi_id)
    episodes = session.scalars(select(DownloadEpisode).where(DownloadEpisode.bangumi_id == bangumi_id)
                              .order_by(DownloadEpisode.episode_number))
    return {"mapping": _mapping_dict(session, mapping) if mapping else {},
            "subscription": _subscription_record(session, sub) if sub else None,
            "episodes": {str(row.episode_number): {**_entry(row), "status": row.status} for row in episodes}}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def read_snapshot(bangumi_id):
    with new_session() as session:
        return snapshot(session, bangumi_id)


def ensure_idle_in_session(session, bangumi_id, *, exclude=None):
    rows = session.scalars(select(MappingChangeTask).where(MappingChangeTask.bangumi_id == bangumi_id))
    for row in rows:
        if row.task_id == exclude:
            continue
        if row.status in {"queued", "running"} or (
                row.kind == "cleanup" and row.status == "failed" and row.payload.get("deletion_started")):
            raise MappingConflict("此订阅有未完成的映射变更，请查看或重试变更任务")


def ensure_idle(bangumi_id):
    with new_session() as session:
        ensure_idle_in_session(session, bangumi_id)


def blocked(bangumi_id):
    try:
        ensure_idle(bangumi_id)
    except MappingConflict:
        return True
    return False


def put(task):
    with new_session() as session, session.begin():
        row = session.get(MappingChangeTask, task["task_id"])
        if row is None:
            row = MappingChangeTask(task_id=task["task_id"], bangumi_id=task["bangumi_id"],
                                    kind=task["kind"], status=task["status"], payload={})
            session.add(row)
        row.status = task["status"]
        row.payload = dict(task)


def get(task_id, bangumi_id):
    with new_session() as session:
        row = session.get(MappingChangeTask, task_id)
        return dict(row.payload) if row and row.bangumi_id == bangumi_id else None


def start_task(task):
    with new_session() as session, session.begin():
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        ensure_idle_in_session(session, task["bangumi_id"])
        if digest(snapshot(session, task["bangumi_id"])) != task["expected"]:
            raise MappingConflict("订阅或下载记录已经变化，请重新发起")
        session.add(MappingChangeTask(task_id=task["task_id"], bangumi_id=task["bangumi_id"],
                                     kind=task["kind"], status=task["status"], payload=dict(task)))


def latest(bangumi_id):
    with new_session() as session:
        rows = session.scalars(select(MappingChangeTask).where(
            MappingChangeTask.bangumi_id == bangumi_id, MappingChangeTask.status != "preview"))
        return max((dict(row.payload) for row in rows), key=lambda task: task["created_at"], default=None)


def claim(task, *, retry=False):
    with new_session() as session, session.begin():
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row = session.get(MappingChangeTask, task["task_id"])
        allowed = {"failed", "partial_failed"} if retry else {"preview"}
        if row is None or row.status not in allowed:
            raise MappingConflict("预览或任务已被使用，请重新打开编辑弹窗")
        ensure_idle_in_session(session, task["bangumi_id"], exclude=row.task_id)
        if not retry and datetime.now(timezone.utc).timestamp() > task["expires_at"]:
            raise MappingConflict("确认预览已过期，请重新保存")
        if digest(snapshot(session, task["bangumi_id"])) != task["expected"]:
            raise MappingConflict("映射、订阅或下载记录已经变化，请重新预览")
        task.update(status="queued", message="等待执行", results=[])
        row.status = "queued"
        row.payload = dict(task)


def verify(task):
    if digest(read_snapshot(task["bangumi_id"])) != task["expected"]:
        raise MappingConflict("映射、订阅或下载记录已经变化，已停止执行，请重新预览")


def finish_cleanup(task):
    with new_session() as session, session.begin():
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        bid = task["bangumi_id"]
        if digest(snapshot(session, bid)) != task["expected"]:
            raise MappingConflict("清理期间订阅发生变化，保留记录以便重试")
        _patch_mapping(session, bid, task["changes"], manual=True)
        for model in (SubscriptionFeed, DownloadEpisode, Subscription):
            session.execute(delete(model).where(model.bangumi_id == bid))
        task.update(status="completed", message="旧文件与订阅已清理，映射已更新，请重新选择字幕组订阅")
        row = session.get(MappingChangeTask, task["task_id"])
        row.status, row.payload = "completed", dict(task)


def save_rebuilt_episode(task, sort, mapping, offsets):
    with new_session() as session, session.begin():
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row = session.get(DownloadEpisode, (task["bangumi_id"], sort))
        if row is None:
            raise MappingConflict("下载记录已删除")
        save_episode_mapping(session, row, mapping)
        if offsets:
            _patch_mapping(session, task["bangumi_id"], offsets, manual=True)
            subscription = session.get(Subscription, task["bangumi_id"])
            if subscription and not subscription.mapping_migrated:
                for field, value in offsets.items():
                    setattr(subscription, field, value)


def initialize():
    with new_session() as session, session.begin():
        rows = session.scalars(select(MappingChangeTask).where(MappingChangeTask.status.in_(["queued", "running"])))
        for row in rows:
            row.status = "failed"
            payload = {**row.payload, "status": "failed", "message": "应用重启，任务已中断，请重试"}
            if row.kind == "nfo":
                payload["mapping_snapshot"] = snapshot(session, row.bangumi_id)["mapping"]
            row.payload = payload
