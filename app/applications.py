from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.accounts import get_active_profile, load_apply_draft_db
from app.models import (
    Application,
    ApplicationEvent,
    ApplicationStatus,
    JobListing,
    ResumeArtifact,
    ResumeGeneration,
    User,
)


_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    ApplicationStatus.DRAFT.value: {
        ApplicationStatus.READY.value,
        ApplicationStatus.SUBMITTED.value,
        ApplicationStatus.WITHDRAWN.value,
    },
    ApplicationStatus.READY.value: {
        ApplicationStatus.SUBMITTED.value,
        ApplicationStatus.WITHDRAWN.value,
    },
    ApplicationStatus.SUBMITTED.value: {
        ApplicationStatus.SCREENING.value,
        ApplicationStatus.INTERVIEW.value,
        ApplicationStatus.OFFER.value,
        ApplicationStatus.REJECTED.value,
        ApplicationStatus.WITHDRAWN.value,
    },
    ApplicationStatus.SCREENING.value: {
        ApplicationStatus.INTERVIEW.value,
        ApplicationStatus.OFFER.value,
        ApplicationStatus.REJECTED.value,
        ApplicationStatus.WITHDRAWN.value,
    },
    ApplicationStatus.INTERVIEW.value: {
        ApplicationStatus.OFFER.value,
        ApplicationStatus.REJECTED.value,
        ApplicationStatus.WITHDRAWN.value,
    },
    ApplicationStatus.OFFER.value: {
        ApplicationStatus.REJECTED.value,
        ApplicationStatus.WITHDRAWN.value,
    },
    ApplicationStatus.REJECTED.value: set(),
    ApplicationStatus.WITHDRAWN.value: set(),
}


def _owned_application(db: Session, user: User, application_id: int) -> Application:
    row = db.get(Application, application_id)
    if row is None or row.user_id != user.id:
        raise PermissionError("Application not found")
    return row


def _answers_json(data: Any) -> str:
    answers = data if isinstance(data, list) else []
    return json.dumps(answers, ensure_ascii=False)


def _latest_resume_artifact(
    db: Session, profile_id: int, listing_id: int
) -> ResumeArtifact | None:
    return (
        db.query(ResumeArtifact)
        .join(ResumeGeneration, ResumeGeneration.id == ResumeArtifact.generation_id)
        .filter(
            ResumeGeneration.profile_id == profile_id,
            ResumeGeneration.listing_id == listing_id,
            ResumeGeneration.status == "completed",
        )
        .order_by(ResumeGeneration.created_at.desc(), ResumeArtifact.id.desc())
        .first()
    )


def get_application(db: Session, user: User, application_id: int) -> Application:
    return _owned_application(db, user, application_id)


def list_applications(
    db: Session,
    user: User,
    *,
    status: str = "",
) -> list[Application]:
    profile = get_active_profile(db, user)
    query = (
        db.query(Application)
        .filter(
            Application.user_id == user.id,
            Application.profile_id == profile.id,
        )
        .order_by(Application.updated_at.desc(), Application.id.desc())
    )
    if status:
        query = query.filter(Application.status == status)
    return query.all()


def get_or_create_application(
    db: Session,
    user: User,
    listing_id: int,
    *,
    channel: str = "manual",
) -> Application:
    profile = get_active_profile(db, user)
    listing = db.get(JobListing, listing_id)
    if listing is None:
        raise ValueError("Job not found")
    if listing.visibility == "private" and listing.owner_user_id != user.id:
        raise PermissionError("Job not found")
    if listing.visibility == "public" and not listing.is_active:
        raise ValueError("Job is no longer active")

    row = (
        db.query(Application)
        .filter(
            Application.profile_id == profile.id,
            Application.listing_id == listing.id,
        )
        .one_or_none()
    )
    if row:
        return row

    draft = load_apply_draft_db(db, user, listing.id)
    cover = str(draft.get("cover_blurb") or "")
    answers = draft.get("answers") if isinstance(draft.get("answers"), list) else []
    row = Application(
        user_id=user.id,
        profile_id=profile.id,
        listing_id=listing.id,
        status=ApplicationStatus.DRAFT.value,
        channel=channel or "manual",
        external_url=listing.url or "",
        cover_blurb=cover,
        answers_json=_answers_json(answers),
        last_status_at=datetime.utcnow(),
    )
    artifact = _latest_resume_artifact(db, profile.id, listing.id)
    if artifact is not None:
        row.resume_artifact_id = artifact.id
    db.add(row)
    db.flush()
    db.add(
        ApplicationEvent(
            application_id=row.id,
            from_status=None,
            to_status=ApplicationStatus.DRAFT.value,
            note="Application created from Apply Assist draft",
        )
    )
    db.flush()
    return row


def refresh_application_snapshot(
    db: Session,
    user: User,
    application_id: int,
) -> Application:
    row = _owned_application(db, user, application_id)
    if row.status not in {
        ApplicationStatus.DRAFT.value,
        ApplicationStatus.READY.value,
    }:
        return row
    if row.listing_id is None:
        return row
    draft = load_apply_draft_db(db, user, row.listing_id)
    if "cover_blurb" in draft:
        row.cover_blurb = str(draft.get("cover_blurb") or "")
    if "answers" in draft:
        row.answers_json = _answers_json(draft.get("answers"))
    artifact = _latest_resume_artifact(db, row.profile_id, row.listing_id)
    if artifact is not None:
        row.resume_artifact_id = artifact.id
    row.updated_at = datetime.utcnow()
    db.flush()
    return row


def transition_application(
    db: Session,
    user: User,
    application_id: int,
    to_status: str,
    *,
    note: str = "",
    external_application_id: str = "",
    external_url: str = "",
) -> Application:
    row = _owned_application(db, user, application_id)
    target = (to_status or "").strip().lower()
    if target not in {item.value for item in ApplicationStatus}:
        raise ValueError("Invalid application status")
    current = row.status
    if target == current:
        if external_application_id:
            row.external_application_id = external_application_id.strip()
        if external_url:
            row.external_url = external_url.strip()
        if note:
            row.notes = note.strip()
        db.flush()
        return row

    allowed = _ALLOWED_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise ValueError(f"Cannot move application from {current} to {target}")

    now = datetime.utcnow()
    row.status = target
    row.last_status_at = now
    if external_application_id:
        row.external_application_id = external_application_id.strip()
    if external_url:
        row.external_url = external_url.strip()
    if note:
        row.notes = note.strip()
    if target == ApplicationStatus.SUBMITTED.value and row.applied_at is None:
        row.applied_at = now

    db.add(
        ApplicationEvent(
            application_id=row.id,
            from_status=current,
            to_status=target,
            note=note.strip(),
        )
    )
    db.add(row)
    db.flush()
    return row


def application_events(
    db: Session, user: User, application_id: int
) -> list[ApplicationEvent]:
    row = _owned_application(db, user, application_id)
    return (
        db.query(ApplicationEvent)
        .filter(ApplicationEvent.application_id == row.id)
        .order_by(ApplicationEvent.created_at.asc(), ApplicationEvent.id.asc())
        .all()
    )


def answers_for_application(row: Application) -> list[dict[str, str]]:
    try:
        data = json.loads(row.answers_json or "[]")
    except json.JSONDecodeError:
        data = []
    if not isinstance(data, list):
        return []
    return [
        {"question": str(item.get("question") or ""), "answer": str(item.get("answer") or "")}
        for item in data
        if isinstance(item, dict) and item.get("question") and item.get("answer")
    ]
