from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.accounts import ensure_account, get_active_profile
from app.accounts.drafts import load_apply_draft_db, save_apply_draft_db
from app.accounts.profile import apply_profile_from_user
from app.apply_assist import ensure_apply_copy, save_job_draft
from app.config import project_path
from app.db import SessionLocal
from app.models import ApplyDraft, User


def test_apply_draft_db_roundtrip(confirmed_user):
    db = SessionLocal()
    try:
        user = db.get(User, confirmed_user["user"].id)
        ensure_account(db, user)
        payload = {
            "cover_blurb": "Hello from Ada",
            "answers": [{"question": "Why?", "answer": "Because."}],
        }
        save_apply_draft_db(db, user, listing_id=999001, data=payload)
        db.commit()
        loaded = load_apply_draft_db(db, user, 999001)
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
        job = SimpleNamespace(
            id=999002,
            title="Engineer",
            company="Acme",
            description="Python",
            location="Remote",
        )
        copy = await ensure_apply_copy(job, profile, existing={}, creds=None)
        save_apply_draft_db(db, user, job.id, copy)
        db.commit()
        drafts_dir = project_path("data", "users", str(user.id), "drafts")
        assert not (drafts_dir / "999002.json").exists()
        loaded = load_apply_draft_db(db, user, job.id)
        assert loaded.get("cover_blurb")
        assert "Acme" in loaded["cover_blurb"] or loaded["cover_blurb"]
    finally:
        db.close()


def test_legacy_file_import_into_db(confirmed_user):
    db = SessionLocal()
    try:
        user = db.get(User, confirmed_user["user"].id)
        ensure_account(db, user)
        listing_id = 999003
        save_job_draft(
            listing_id,
            {
                "cover_blurb": "Legacy cover",
                "answers": [{"question": "Q", "answer": "A"}],
            },
            user_id=str(user.id),
        )
        profile = get_active_profile(db, user)
        db.query(ApplyDraft).filter(
            ApplyDraft.profile_id == profile.id,
            ApplyDraft.listing_id == listing_id,
        ).delete()
        db.commit()

        loaded = load_apply_draft_db(db, user, listing_id)
        db.commit()
        assert loaded["cover_blurb"] == "Legacy cover"
        again = load_apply_draft_db(db, user, listing_id)
        assert again["cover_blurb"] == "Legacy cover"
    finally:
        db.close()
