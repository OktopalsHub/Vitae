from datetime import datetime, timedelta

from app.models import WorkerJobStatus
from app.workers.queue import enqueue_job


def test_worker_operations_requires_admin(client, db_session):
    response = client.get("/admin/operations/workers")
    assert response.status_code in {302, 303}


def test_worker_operations_reports_counts(admin_client, db_session):
    enqueue_job(db_session, kind="ops-test")
    db_session.commit()

    response = admin_client.get("/admin/operations/workers")
    assert response.status_code == 200
    payload = response.json()
    assert payload["counts"][WorkerJobStatus.QUEUED.value] >= 1
    assert "stale_running" in payload
