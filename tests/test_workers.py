from datetime import datetime, timedelta

from app.models import WorkerJobStatus
from app.workers.queue import claim_next_job, complete_job, enqueue_job, fail_job, heartbeat_job


def test_worker_job_claim_complete(db_session):
    job = enqueue_job(db_session, kind="test", payload={"value": 1}, idempotency_key="worker-test-1")
    db_session.commit()
    claimed = claim_next_job(db_session)
    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == WorkerJobStatus.RUNNING.value
    assert claimed.last_heartbeat_at is not None
    db_session.commit()
    current = db_session.get(type(job), job.id)
    complete_job(db_session, current)
    db_session.commit()
    assert current.status == WorkerJobStatus.COMPLETED.value


def test_worker_job_retry_then_dead(db_session):
    job = enqueue_job(db_session, kind="test", max_attempts=1)
    db_session.commit()
    claimed = claim_next_job(db_session)
    db_session.commit()
    current = db_session.get(type(job), job.id)
    fail_job(db_session, current, "boom")
    db_session.commit()
    assert current.status == WorkerJobStatus.DEAD.value


def test_worker_heartbeat_updates_lease(db_session):
    job = enqueue_job(db_session, kind="heartbeat")
    db_session.commit()
    claimed = claim_next_job(db_session)
    db_session.commit()
    current = db_session.get(type(job), job.id)
    old_heartbeat = current.last_heartbeat_at

    assert heartbeat_job(db_session, current, lock_owner=current.lock_owner) is True
    db_session.commit()
    assert current.last_heartbeat_at >= old_heartbeat


def test_stale_worker_is_reclaimed_from_heartbeat(db_session):
    job = enqueue_job(db_session, kind="stale")
    db_session.commit()
    first = claim_next_job(db_session, lease_seconds=60)
    db_session.commit()
    original_owner = first.lock_owner

    current = db_session.get(type(job), job.id)
    stale_at = datetime.utcnow() - timedelta(minutes=10)
    current.locked_at = stale_at
    current.last_heartbeat_at = stale_at
    db_session.commit()

    reclaimed = claim_next_job(db_session, lease_seconds=60)
    assert reclaimed is not None
    assert reclaimed.id == first.id
    assert reclaimed.attempts == 2
    assert reclaimed.lock_owner != original_owner


def test_heartbeat_rejects_stale_lock_owner(db_session):
    job = enqueue_job(db_session, kind="ownership")
    db_session.commit()
    claimed = claim_next_job(db_session)
    db_session.commit()
    current = db_session.get(type(job), claimed.id)
    before = current.last_heartbeat_at

    assert heartbeat_job(db_session, current, lock_owner="another-worker") is False
    assert current.last_heartbeat_at == before
