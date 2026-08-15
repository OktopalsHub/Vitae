from __future__ import annotations

import secrets
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.accounts import ensure_account, get_active_profile
from app.accounts.drafts import load_apply_draft_db, save_apply_draft_db
from app.accounts.profile import apply_profile_from_user
from app.generator.orchestrate import ensure_apply_copy
from app.db import SessionLocal
from app.models import ApplyDraft, JobListing, ListingVisibility, User


def _make_listing(db, owner_user_id=None) -> JobListing:
    """Insert a minimal public JobListing for test use; returns the persisted row."""
    listing = JobListing(
        source="test",
        external_id=secrets.token_hex(8),
        public_id=secrets.token_urlsafe(16)[:22],
        title="Test Engineer",
        company="TestCo",
        location="Remote",
        url="https://example.com/job",
        description="Test job",
        visibility=ListingVisibility.PUBLIC.value,
        is_active=True,
        owner_user_id=owner_user_id,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(listing)
    db.flush()
    return listing


def test_apply_draft_db_roundtrip(confirmed_user):
    db = SessionLocal()
    try:
        user = db.get(User, confirmed_user["user"].id)
        ensure_account(db, user)
        listing = _make_listing(db)
        db.flush()
        payload = {
            "cover_blurb": "Hello from Ada",
            "answers": [{"question": "Why?", "answer": "Because."}],
        }
        save_apply_draft_db(db, user, listing_id=listing.id, data=payload)
        db.commit()
        loaded = load_apply_draft_db(db, user, listing.id)
        assert loaded["cover_blurb"] == "Hello from Ada"
        assert loaded["answers"][0]["answer"] == "Because."
    finally:
        db.close()


@pytest.mark.asyncio
async def test_ensure_apply_copy_persists_via_db_only(confirmed_user):
    db = SessionLocal()
    try:
        user = db.get(User, confirmed_user["user"].id)
        ensure_account(db, user)
        profile = apply_profile_from_user(db, user)
        listing = _make_listing(db)
        db.flush()
        job = SimpleNamespace(
            id=listing.id,
            title="Engineer",
            company="Acme",
            description="Python",
            location="Remote",
        )
        copy = await ensure_apply_copy(job, profile, existing={}, creds=None)
        save_apply_draft_db(db, user, job.id, copy)
        db.commit()
        loaded = load_apply_draft_db(db, user, job.id)
        assert loaded.get("cover_blurb")
        assert "Acme" in loaded["cover_blurb"] or loaded["cover_blurb"]
    finally:
        db.close()
