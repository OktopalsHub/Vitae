from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from app.applications import get_or_create_application
from app.models import (
    ApplicationStatus,
    AutoApplyItem,
    AutoApplyItemStatus,
    AutoApplyRun,
    AutoApplyRunStatus,
    JobListing,
    User,
)


def _owned_run(db: Session, user: User, run_id: int) -> AutoApplyRun:
    run = db.get(AutoApplyRun, run_id)
    if run is None or run.user_id != user.id:
        raise PermissionError("Auto-apply run not found")
    return run


def create_run(
    db: Session,
    user: User,
    listing_ids: list[int],
    *,
    max_applications: int = 10,
    requires_review: bool = True,
    idempotency_key: str = "",
) -> AutoApplyRun:
    from app.accounts import get_active_profile

    profile = get_active_profile(db, user)
    key = (idempotency_key or "").strip() or f"{user.id}:{profile.id}:{uuid4().hex}"
    existing = db.query(AutoApplyRun).filter(AutoApplyRun.idempotency_key == key).one_or_none()
    if existing is not None:
        if existing.user_id != user.id:
            raise PermissionError("Invalid idempotency key")
        return existing

    limit = max(1, min(int(max_applications), 100))
    unique_ids = list(dict.fromkeys(int(item) for item in listing_ids))[:limit]
    if not unique_ids:
        raise ValueError("At least one job is required")

    listings = db.query(JobListing).filter(JobListing.id.in_(unique_ids)).all()
    by_id = {item.id: item for item in listings}
    missing = [item for item in unique_ids if item not in by_id]
    if missing:
        raise ValueError("One or more jobs were not found")
    inactive = [item for item in listings if not item.is_active and item.visibility == "public"]
    if inactive:
        raise ValueError("One or more jobs are no longer active")

    run = AutoApplyRun(
        user_id=user.id,
        profile_id=profile.id,
        status=AutoApplyRunStatus.QUEUED.value,
        max_applications=limit,
        requires_review=requires_review,
        idempotency_key=key,
    )
    db.add(run)
    db.flush()

    for listing_id in unique_ids:
        application = get_or_create_application(db, user, listing_id, channel="auto_apply")
        db.add(
            AutoApplyItem(
                run_id=run.id,
                application_id=application.id,
                listing_id=listing_id,
                status=AutoApplyItemStatus.QUEUED.value,
                requires_review=requires_review,
            )
        )
    db.flush()
    return run


def get_run(db: Session, user: User, run_id: int) -> AutoApplyRun:
    return _owned_run(db, user, run_id)


def list_runs(db: Session, user: User) -> list[AutoApplyRun]:
    return (
        db.query(AutoApplyRun)
        .filter(AutoApplyRun.user_id == user.id)
        .order_by(AutoApplyRun.created_at.desc(), AutoApplyRun.id.desc())
        .all()
    )


def list_items(db: Session, user: User, run_id: int) -> list[AutoApplyItem]:
    run = _owned_run(db, user, run_id)
    return (
        db.query(AutoApplyItem)
        .filter(AutoApplyItem.run_id == run.id)
        .order_by(AutoApplyItem.created_at.asc(), AutoApplyItem.id.asc())
        .all()
    )


def start_run(db: Session, user: User, run_id: int) -> AutoApplyRun:
    run = _owned_run(db, user, run_id)
    if run.status not in {
        AutoApplyRunStatus.QUEUED.value,
        AutoApplyRunStatus.PAUSED.value,
    }:
        raise ValueError("Only queued or paused runs can be started")
    run.status = AutoApplyRunStatus.RUNNING.value
    run.started_at = run.started_at or datetime.utcnow()
    run.updated_at = datetime.utcnow()
    db.flush()
    return run


def pause_run(db: Session, user: User, run_id: int) -> AutoApplyRun:
    run = _owned_run(db, user, run_id)
    if run.status != AutoApplyRunStatus.RUNNING.value:
        raise ValueError("Only running runs can be paused")
    run.status = AutoApplyRunStatus.PAUSED.value
    run.updated_at = datetime.utcnow()
    db.flush()
    return run


def cancel_run(db: Session, user: User, run_id: int) -> AutoApplyRun:
    run = _owned_run(db, user, run_id)
    if run.status in {
        AutoApplyRunStatus.COMPLETED.value,
        AutoApplyRunStatus.FAILED.value,
        AutoApplyRunStatus.CANCELLED.value,
    }:
        return run
    run.status = AutoApplyRunStatus.CANCELLED.value
    run.completed_at = datetime.utcnow()
    run.updated_at = datetime.utcnow()
    db.query(AutoApplyItem).filter(
        AutoApplyItem.run_id == run.id,
        AutoApplyItem.status.in_([
            AutoApplyItemStatus.QUEUED.value,
            AutoApplyItemStatus.PREPARING.value,
            AutoApplyItemStatus.READY.value,
            AutoApplyItemStatus.NEEDS_REVIEW.value,
        ]),
    ).update(
        {AutoApplyItem.status: AutoApplyItemStatus.SKIPPED.value},
        synchronize_session=False,
    )
    db.flush()
    return run


def mark_item_review(
    db: Session,
    user: User,
    item_id: int,
    *,
    reason: str,
) -> AutoApplyItem:
    item = db.get(AutoApplyItem, item_id)
    if item is None:
        raise PermissionError("Auto-apply item not found")
    run = _owned_run(db, user, item.run_id)
    if run.status in {
        AutoApplyRunStatus.CANCELLED.value,
        AutoApplyRunStatus.COMPLETED.value,
        AutoApplyRunStatus.FAILED.value,
    }:
        raise ValueError("Run is no longer active")
    item.status = AutoApplyItemStatus.NEEDS_REVIEW.value
    item.requires_review = True
    item.review_reason = (reason or "User review required").strip()[:2000]
    item.updated_at = datetime.utcnow()
    db.flush()
    return item


def mark_item_submitted(
    db: Session,
    user: User,
    item_id: int,
    *,
    external_application_id: str = "",
    external_url: str = "",
    note: str = "",
) -> AutoApplyItem:
    item = db.get(AutoApplyItem, item_id)
    if item is None:
        raise PermissionError("Auto-apply item not found")
    run = _owned_run(db, user, item.run_id)
    if run.status in {
        AutoApplyRunStatus.CANCELLED.value,
        AutoApplyRunStatus.FAILED.value,
        AutoApplyRunStatus.COMPLETED.value,
    }:
        raise ValueError("Run is no longer active")
    if item.application_id is None:
        raise ValueError("Application is missing")
    from app.applications import transition_application

    application = transition_application(
        db,
        user,
        item.application_id,
        ApplicationStatus.SUBMITTED.value,
        external_application_id=external_application_id,
        external_url=external_url,
        note=note,
    )
    item.status = AutoApplyItemStatus.SUBMITTED.value
    item.attempt_count += 1
    item.last_error = ""
    item.updated_at = datetime.utcnow()
    run.submitted_count += 1
    run.updated_at = datetime.utcnow()
    db.flush()
    return item
