"""Bind shared recovery checkpoints to an asynchronous workflow execution."""

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar

from ...db.recovery import RecoveryJob

_current: ContextVar[RecoveryJob | None] = ContextVar("recovery_job", default=None)


def current_job() -> RecoveryJob:
    job = _current.get()
    if job is None:
        raise RuntimeError("workflow_checkpoint_requires_owned_job")
    return job


@asynccontextmanager
async def execution(job: RecoveryJob):
    token = _current.set(job)
    owner_task = asyncio.current_task()

    async def renew():
        try:
            while True:
                await asyncio.sleep(30)
                job.heartbeat()
        except Exception:
            owner_task.cancel()
            raise

    heartbeat = asyncio.create_task(renew())
    try:
        yield job
    finally:
        heartbeat.cancel()
        result = await asyncio.gather(heartbeat, return_exceptions=True)
        _current.reset(token)
        if result and isinstance(result[0], Exception):
            raise result[0]
