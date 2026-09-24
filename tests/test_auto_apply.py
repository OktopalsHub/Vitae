from app.auto_apply import create_run, mark_item_review, start_run
from app.models import AutoApplyItem, AutoApplyItemStatus, AutoApplyRunStatus


def _listing(db, user, suffix):
    from app.models import JobListing
    row = JobListing(
        public_id=f"auto-apply-job-{user.id}-{suffix}",
        source="test",
        external_id=f"auto-apply-{user.id}-{suffix}",
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


def test_auto_apply_run_creation_is_idempotent(db_session, confirmed_user):
    user = confirmed_user["user"]
    listing = _listing(db_session, user, "one")
    key = f"auto:{user.id}:one"

    first = create_run(db_session, user, [listing.id], idempotency_key=key)
    second = create_run(db_session, user, [listing.id], idempotency_key=key)

    assert first.id == second.id
    assert len(db_session.query(AutoApplyItem).filter(AutoApplyItem.run_id == first.id).all()) == 1


def test_auto_apply_requires_explicit_submission(db_session, confirmed_user):
    user = confirmed_user["user"]
    listing = _listing(db_session, user, "two")

    run = create_run(db_session, user, [listing.id], requires_review=True)
    assert run.status == AutoApplyRunStatus.QUEUED.value

    start_run(db_session, user, run.id)
    item = db_session.query(AutoApplyItem).filter(AutoApplyItem.run_id == run.id).one()
    assert item.status == AutoApplyItemStatus.QUEUED.value
    assert item.requires_review is True


def test_auto_apply_review_is_user_scoped(db_session, confirmed_user):
    user = confirmed_user["user"]
    listing = _listing(db_session, user, "three")
    run = create_run(db_session, user, [listing.id])
    item = db_session.query(AutoApplyItem).filter(AutoApplyItem.run_id == run.id).one()

    updated = mark_item_review(db_session, user, item.id, reason="Employer site needs manual review")
    assert updated.status == AutoApplyItemStatus.NEEDS_REVIEW.value
    assert updated.review_reason == "Employer site needs manual review"
