from __future__ import annotations

from app.admin_audit import record_admin_action
from app.models import User


def test_admin_audit_records_privileged_action(db_session):
    actor = User(email="audit@example.com", hashed_password="x", is_active=True, is_verified=True)
    db_session.add(actor)
    db_session.flush()
    event = record_admin_action(
        db_session,
        actor,
        action="worker_job.retried",
        resource_type="worker_job",
        resource_id="42",
        metadata={"queue": "default"},
    )
    db_session.commit()
    assert event.id is not None
    assert event.actor_user_id == actor.id
    assert event.action == "worker_job.retried"
    assert '"queue":"default"' in event.metadata_json
    assert "secret" not in event.metadata_json.lower()


def test_role_change_route_records_audit_event(confirmed_user, client, db_session):
    from app.models import AdminAuditEvent, User, UserRole

    actor = confirmed_user["user"]
    actor.role = UserRole.SUPER_ADMIN.value
    db_session.add(actor)

    target = User(
        email="role-target@example.com",
        hashed_password="x",
        is_active=True,
        is_verified=False,
        role=UserRole.BASIC.value,
    )
    db_session.add(target)
    db_session.commit()
    db_session.refresh(target)

    response = client.post(
        f"/admin/users/{target.id}/role",
        data={"role": UserRole.ADMIN.value, "csrf_token": confirmed_user["csrf_token"]},
        follow_redirects=False,
    )

    assert response.status_code in {302, 303}
    event = (
        db_session.query(AdminAuditEvent)
        .filter(
            AdminAuditEvent.action == "user.role_updated",
            AdminAuditEvent.resource_id == str(target.id),
        )
        .one()
    )
    assert event.actor_user_id == actor.id


def test_dead_worker_retry_route_records_audit_event(confirmed_user, client, db_session):
    from app.models import AdminAuditEvent, UserRole, WorkerJob, WorkerJobStatus

    actor = confirmed_user["user"]
    actor.role = UserRole.ADMIN.value
    db_session.add(actor)

    job = WorkerJob(
        queue="default",
        kind="test.retry",
        payload_json='{"id": 1}',
        payload_hash="test-hash",
        status=WorkerJobStatus.DEAD.value,
        attempts=5,
        max_attempts=5,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    response = client.post(
        f"/admin/operations/workers/{job.id}/retry",
        data={"csrf_token": confirmed_user["csrf_token"]},
        follow_redirects=False,
    )

    assert response.status_code in {302, 303}
    event = (
        db_session.query(AdminAuditEvent)
        .filter(
            AdminAuditEvent.action == "worker_job.retried",
            AdminAuditEvent.resource_id == str(job.id),
        )
        .one()
    )
    assert event.actor_user_id == actor.id
