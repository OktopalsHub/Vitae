from __future__ import annotations

import hashlib
import json
import logging
import socket
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import WorkerJob, WorkerJobStatus

log = logging.getLogger(__name__)
DEFAULT_LEASE_SECONDS = 300
MAX_ERROR_CHARS = 4000


def _payload_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload or {}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def enqueue_job(
    db: Session,
    *,
    kind: str,
    payload: dict[str, Any] | None = None,
    queue: str = "default",
    idempotency_key: str | None = None,
    available_at: datetime | None = None,
    max_attempts: int = 5,
) -> WorkerJob:
    payload = payload or {}
    key = (idempotency_key or "").strip()
    if key:
        existing = db.query(WorkerJob).filter(WorkerJob.idempotency_key == key).one_or_none()
        if existing:
            return existing
    job = WorkerJob(
        queue=queue,
        kind=kind,
        payload_json=json.dumps(payload, ensure_ascii=False, default=str),
        payload_hash=_payload_hash(payload),
        status=WorkerJobStatus.QUEUED.value,
        attempts=0,
        max_attempts=max(1, min(int(max_attempts), 20)),
        idempotency_key=key or None,
        available_at=available_at or datetime.utcnow(),
    )
    db.add(job)
    db.flush()
    return job


def claim_next_job(db: Session, *, queue: str = "default", lease_seconds: int = DEFAULT_LEASE_SECONDS) -> WorkerJob | None:
    now = datetime.utcnow()
    stale = now - timedelta(seconds=max(30, lease_seconds))
    job = (
        db.query(WorkerJob)
        .filter(
            WorkerJob.queue == queue,
            or_(
                (WorkerJob.status == WorkerJobStatus.QUEUED.value) & (WorkerJob.available_at <= now),
                (WorkerJob.status == WorkerJobStatus.RUNNING.value) & (WorkerJob.locked_at < stale),
            ),
        )
        .order_by(WorkerJob.priority.desc(), WorkerJob.created_at.asc())
        .with_for_update(skip_locked=True)
        .first()
    )
    if job is None:
        return None
    job.status = WorkerJobStatus.RUNNING.value
    job.attempts += 1
    job.locked_at = now
    job.lock_owner = socket.gethostname() + ":" + str(uuid.uuid4())
    job.last_heartbeat_at = now
    db.flush()
    return job


def complete_job(db: Session, job: WorkerJob) -> None:
    job.status = WorkerJobStatus.COMPLETED.value
    job.completed_at = datetime.utcnow()
    job.locked_at = None
    job.lock_owner = ""
    job.last_error = ""
    db.flush()


def fail_job(db: Session, job: WorkerJob, error: Exception | str, *, retry_delay_seconds: int = 60) -> None:
    message = str(error)[:MAX_ERROR_CHARS]
    job.last_error = message
    job.locked_at = None
    job.lock_owner = ""
    if job.attempts < job.max_attempts:
        job.status = WorkerJobStatus.QUEUED.value
        backoff = min(max(retry_delay_seconds, 5) * (2 ** max(0, job.attempts - 1)), 3600)
        job.available_at = datetime.utcnow() + timedelta(seconds=backoff)
    else:
        job.status = WorkerJobStatus.DEAD.value
        job.failed_at = datetime.utcnow()
    db.flush()


def heartbeat_job(db: Session, job: WorkerJob) -> None:
    job.last_heartbeat_at = datetime.utcnow()
    db.flush()
