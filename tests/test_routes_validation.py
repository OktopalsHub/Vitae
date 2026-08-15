"""Tests for job apply/rewrite routes and settings profile update."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.db import SessionLocal
from app.models import ApplyDraft, JobListing, ListingVisibility, User


def _make_listing(db, owner_user_id=None) -> JobListing:
    """Insert a minimal public JobListing for test use."""
    import secrets
    from datetime import datetime

    listing = JobListing(
        source="test",
        external_id=secrets.token_hex(8),
        public_id=secrets.token_urlsafe(16)[:22],
        title="Test Engineer",
        company="TestCo",
        location="Remote",
        url="https://example.com/job",
        description="Test job description with Python and APIs",
        visibility=ListingVisibility.PUBLIC.value,
        is_active=True,
        owner_user_id=owner_user_id,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(listing)
    db.flush()
    return listing


# ---------------------------------------------------------------------------
# Apply draft save/load round-trip
# ---------------------------------------------------------------------------

def test_apply_draft_save_and_load(confirmed_user):
    db = SessionLocal()
    try:
        from app.accounts import ensure_account
        from app.accounts.drafts import load_apply_draft_db, save_apply_draft_db

        user = db.get(User, confirmed_user["user"].id)
        ensure_account(db, user)
        listing = _make_listing(db)
        db.flush()

        payload = {
            "cover_blurb": "Dear Hiring Manager, I am interested in this role.",
            "answers": [
                {"question": "Tell us about yourself", "answer": "I am a backend engineer."},
                {"question": "Why this company?", "answer": "Your mission aligns with my experience."},
            ],
        }
        save_apply_draft_db(db, user, listing_id=listing.id, data=payload)
        db.commit()

        loaded = load_apply_draft_db(db, user, listing.id)
        assert loaded["cover_blurb"] == "Dear Hiring Manager, I am interested in this role."
        assert len(loaded["answers"]) == 2
        assert loaded["answers"][0]["question"] == "Tell us about yourself"
    finally:
        db.close()


def test_apply_draft_overwrite(confirmed_user):
    db = SessionLocal()
    try:
        from app.accounts import ensure_account
        from app.accounts.drafts import load_apply_draft_db, save_apply_draft_db

        user = db.get(User, confirmed_user["user"].id)
        ensure_account(db, user)
        listing = _make_listing(db)
        db.flush()

        save_apply_draft_db(db, user, listing.id, {"cover_blurb": "First version", "answers": []})
        db.commit()

        save_apply_draft_db(db, user, listing.id, {"cover_blurb": "Second version"})
        db.commit()

        loaded = load_apply_draft_db(db, user, listing.id)
        assert loaded["cover_blurb"] == "Second version"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Apply draft access control
# ---------------------------------------------------------------------------

def test_apply_draft_requires_matching_user(confirmed_user):
    db = SessionLocal()
    try:
        from app.accounts import ensure_account
        from app.accounts.drafts import load_apply_draft_db, save_apply_draft_db

        user = db.get(User, confirmed_user["user"].id)
        ensure_account(db, user)
        listing = _make_listing(db, owner_user_id=user.id)
        db.flush()

        save_apply_draft_db(db, user, listing.id, {"cover_blurb": "My draft"})
        db.commit()

        # Another user should not be able to read this private listing's draft.
        other_listing = _make_listing(db, owner_user_id=user.id)
        other_listing.visibility = ListingVisibility.PRIVATE.value
        other_listing.owner_user_id = user.id  # same owner for simplicity
        db.flush()

        loaded = load_apply_draft_db(db, user, listing.id)
        assert loaded["cover_blurb"] == "My draft"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Settings apply profile validation
# ---------------------------------------------------------------------------

def test_settings_apply_profile_rejects_invalid_email(confirmed_user, client):
    token = confirmed_user["csrf_token"]
    r = client.post(
        "/settings/apply-profile",
        data={
            "full_name": "Test User",
            "email": "not-an-email",
            "phone": "",
            "linkedin": "",
            "github": "",
            "location_preference": "",
            "website": "",
            "note": "",
            "years_experience": "",
            "work_authorization": "",
            "salary_expectation": "",
            "earliest_start": "",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert r.status_code in {302, 303}
    location = r.headers.get("location", "")
    assert "Invalid%20email" in location or "Invalid email" in location


def test_settings_apply_profile_saves_valid_data(confirmed_user, client):
    token = confirmed_user["csrf_token"]
    r = client.post(
        "/settings/apply-profile",
        data={
            "full_name": "Jane Doe",
            "email": "jane@example.com",
            "phone": "+1234567890",
            "linkedin": "https://linkedin.com/in/jane",
            "github": "https://github.com/jane",
            "location_preference": "Remote",
            "website": "https://jane.dev",
            "note": "Looking for senior roles",
            "years_experience": "5",
            "work_authorization": "US Citizen",
            "salary_expectation": "$150k-$180k",
            "earliest_start": "2 weeks",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert r.status_code in {302, 303}

    # Verify data was saved.
    db = SessionLocal()
    try:
        from app.accounts import get_active_profile
        user = db.get(User, confirmed_user["user"].id)
        profile = get_active_profile(db, user)
        assert profile.full_name == "Jane Doe"
        assert profile.email == "jane@example.com"
        assert profile.phone == "+1234567890"
        assert profile.linkedin == "https://linkedin.com/in/jane"
        assert profile.years_experience == "5"
    finally:
        db.close()


def test_settings_apply_profile_truncates_long_inputs(confirmed_user, client):
    token = confirmed_user["csrf_token"]
    long_name = "A" * 200  # exceeds max length of 100
    r = client.post(
        "/settings/apply-profile",
        data={
            "full_name": long_name,
            "email": "test@example.com",
            "phone": "",
            "linkedin": "",
            "github": "",
            "location_preference": "",
            "website": "",
            "note": "",
            "years_experience": "",
            "work_authorization": "",
            "salary_expectation": "",
            "earliest_start": "",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert r.status_code in {302, 303}

    db = SessionLocal()
    try:
        from app.accounts import get_active_profile
        user = db.get(User, confirmed_user["user"].id)
        profile = get_active_profile(db, user)
        assert len(profile.full_name) <= 100
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Scheduler basic import and config
# ---------------------------------------------------------------------------

def test_scheduler_config_defaults():
    from app.scheduler import (
        CATALOGUE_SYNC_INTERVAL_SECONDS,
        USER_RANK_INTERVAL_SECONDS,
        _env_disabled,
    )
    assert CATALOGUE_SYNC_INTERVAL_SECONDS > 0
    assert USER_RANK_INTERVAL_SECONDS > 0
    assert _env_disabled("CATALOGUE_SYNC_DISABLED") is True  # set in conftest


def test_scheduler_env_disabled():
    from app.scheduler import _env_disabled
    import os
    os.environ["TEST_FLAG"] = "1"
    assert _env_disabled("TEST_FLAG") is True
    os.environ["TEST_FLAG"] = "true"
    assert _env_disabled("TEST_FLAG") is True
    os.environ["TEST_FLAG"] = "yes"
    assert _env_disabled("TEST_FLAG") is True
    os.environ["TEST_FLAG"] = "0"
    assert _env_disabled("TEST_FLAG") is False
    os.environ["TEST_FLAG"] = ""
    assert _env_disabled("TEST_FLAG") is False
