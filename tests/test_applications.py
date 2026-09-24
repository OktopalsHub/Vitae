from __future__ import annotations

from app.applications import (
    answers_for_application,
    get_or_create_application,
    transition_application,
)
from app.models import ApplicationStatus, JobListing


def _listing(db, user):
    row = JobListing(
        public_id="application-test-job",
        source="test",
        external_id="application-test",
        title="Backend Engineer",
        company="Acme",
        location="Remote",
        url="https://example.com/jobs/backend",
        description="Build APIs",
        scope_key="public",
        visibility="public",
        is_active=True,
        source_key="test",
    )
    db.add(row)
    db.commit()
    return row


def test_application_creation_is_idempotent(db_session, confirmed_user):
    user = confirmed_user["user"]
    listing = _listing(db_session, user)

    first = get_or_create_application(db_session, user, listing.id)
    second = get_or_create_application(db_session, user, listing.id)

    assert first.id == second.id
    assert first.status == ApplicationStatus.DRAFT.value


def test_application_transition_records_submission(db_session, confirmed_user):
    user = confirmed_user["user"]
    listing = _listing(db_session, user)

    row = get_or_create_application(db_session, user, listing.id)
    transition_application(
        db_session,
        user,
        row.id,
        ApplicationStatus.SUBMITTED.value,
        external_application_id="ACME-123",
    )
    db_session.commit()

    db_session.refresh(row)
    assert row.status == ApplicationStatus.SUBMITTED.value
    assert row.applied_at is not None
    assert row.external_application_id == "ACME-123"
    assert len(row.__class__.metadata.tables["application_events"].columns) > 0


def test_terminal_application_cannot_move_back(db_session, confirmed_user):
    user = confirmed_user["user"]
    listing = _listing(db_session, user)

    row = get_or_create_application(db_session, user, listing.id)
    transition_application(
        db_session,
        user,
        row.id,
        ApplicationStatus.SUBMITTED.value,
    )
    transition_application(
        db_session,
        user,
        row.id,
        ApplicationStatus.REJECTED.value,
    )

    try:
        transition_application(
            db_session,
            user,
            row.id,
            ApplicationStatus.INTERVIEW.value,
        )
    except ValueError as exc:
        assert "Cannot move application" in str(exc)
    else:
        raise AssertionError("terminal application accepted an invalid transition")


def test_answers_parser_is_safe(db_session, confirmed_user):
    user = confirmed_user["user"]
    listing = _listing(db_session, user)
    row = get_or_create_application(db_session, user, listing.id)
    row.answers_json = '[{"question":"Why?","answer":"Because"}]'
    db_session.commit()

    assert answers_for_application(row) == [{"question": "Why?", "answer": "Because"}]
