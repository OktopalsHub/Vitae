from app.models import WorkerJobStatus
from app.workers.queue import claim_next_job, complete_job, enqueue_job, fail_job

def test_worker_job_claim_complete(db_session):
    job = enqueue_job(db_session, kind="test", payload={"value": 1}, idempotency_key="worker-test-1")
    db_session.commit()
    claimed = claim_next_job(db_session)
    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == WorkerJobStatus.RUNNING.value
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
