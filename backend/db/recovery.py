"""Shared storage for unfinished workflows, with atomic claims and checkpoints."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import logging
from uuid import uuid4

from sqlalchemy import delete, or_, select, update

from .connection import new_session
from .models import WorkflowRecovery

LEASE_SECONDS = 120
logger = logging.getLogger(__name__)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def lease_deadline() -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=LEASE_SECONDS)).isoformat(timespec="microseconds")


def read(session, workflow_type: str, business_key: str):
    return session.scalar(select(WorkflowRecovery).where(
        WorkflowRecovery.workflow_type == workflow_type,
        WorkflowRecovery.business_key == business_key))


def create(session, workflow_type: str, business_key: str, stage: str, checkpoint: dict):
    if read(session, workflow_type, business_key) is not None:
        raise ValueError("workflow_already_pending")
    timestamp = now()
    row = WorkflowRecovery(id=uuid4().hex, workflow_type=workflow_type,
                           business_key=business_key, stage=stage, checkpoint=deepcopy(checkpoint),
                           created_at=timestamp, updated_at=timestamp)
    session.add(row)
    session.flush()
    return row


def pending(workflow_type: str) -> list[str]:
    timestamp = now()
    with new_session() as session:
        return list(session.scalars(select(WorkflowRecovery.business_key).where(
            WorkflowRecovery.workflow_type == workflow_type,
            or_(WorkflowRecovery.next_run_at.is_(None), WorkflowRecovery.next_run_at <= timestamp),
            or_(WorkflowRecovery.lease_owner.is_(None), WorkflowRecovery.lease_expires_at <= timestamp))))


class LostLease(RuntimeError):
    pass


class RecoveryJob:
    """An owned workflow. Every write checks its revision and lease identity."""

    def __init__(self, row, owner):
        self.id, self.owner = row.id, owner
        self.workflow_type, self.business_key = row.workflow_type, row.business_key
        self.revision, self.stage = row.revision, row.stage
        self.checkpoint = deepcopy(row.checkpoint)

    @classmethod
    def claim(cls, workflow_type: str, business_key: str):
        owner, timestamp = uuid4().hex, now()
        with new_session() as session, session.begin():
            row = read(session, workflow_type, business_key)
            if row is None:
                return None
            if row.schema_version != 1:
                raise ValueError("unsupported_workflow_checkpoint_version")
            result = session.execute(update(WorkflowRecovery).where(
                WorkflowRecovery.id == row.id, WorkflowRecovery.revision == row.revision,
                or_(WorkflowRecovery.lease_owner.is_(None), WorkflowRecovery.lease_expires_at <= timestamp),
                or_(WorkflowRecovery.next_run_at.is_(None), WorkflowRecovery.next_run_at <= timestamp)
            ).values(lease_owner=owner, lease_expires_at=lease_deadline(), state="running",
                     revision=row.revision + 1, updated_at=timestamp),
                execution_options={"synchronize_session": False})
            if result.rowcount != 1:
                return None
            session.refresh(row)
            return cls(row, owner)

    def _owned(self):
        return (WorkflowRecovery.id == self.id, WorkflowRecovery.lease_owner == self.owner,
                WorkflowRecovery.revision == self.revision,
                WorkflowRecovery.lease_expires_at > now())

    def save(self, *, stage: str | None = None):
        next_stage = stage or self.stage
        with new_session() as session, session.begin():
            result = session.execute(update(WorkflowRecovery).where(*self._owned()).values(
                checkpoint=deepcopy(self.checkpoint), stage=next_stage,
                revision=self.revision + 1, updated_at=now(), lease_expires_at=lease_deadline()))
            if result.rowcount != 1:
                raise LostLease("workflow_lease_lost")
        self.revision += 1
        self.stage = next_stage
        logger.debug("workflow.checkpoint.saved workflow=%s type=%s business_key=%s stage=%s revision=%d",
                     self.id, self.workflow_type, self.business_key, self.stage, self.revision)

    def heartbeat(self):
        with new_session() as session, session.begin():
            result = session.execute(update(WorkflowRecovery).where(*self._owned()).values(
                lease_expires_at=lease_deadline()))
            if result.rowcount != 1:
                raise LostLease("workflow_lease_lost")

    def guard(self, session):
        """Lock and validate ownership before committing a business side effect."""
        result = session.execute(update(WorkflowRecovery).where(*self._owned()).values(
            updated_at=now()))
        if result.rowcount != 1:
            raise LostLease("workflow_lease_lost")

    def release(self, *, blocked: bool = False, delay_seconds: int = 0):
        deadline = ((datetime.now(timezone.utc) + timedelta(seconds=delay_seconds))
                    .isoformat(timespec="microseconds")) if delay_seconds else None
        with new_session() as session, session.begin():
            result = session.execute(update(WorkflowRecovery).where(*self._owned()).values(
                state="blocked" if blocked else "waiting", lease_owner=None, lease_expires_at=None,
                next_run_at=deadline, updated_at=now(), revision=self.revision + 1))
            if result.rowcount != 1:
                raise LostLease("workflow_lease_lost")
        self.revision += 1

    def delete(self, session):
        result = session.execute(delete(WorkflowRecovery).where(*self._owned()))
        if result.rowcount != 1:
            raise LostLease("workflow_lease_lost")
