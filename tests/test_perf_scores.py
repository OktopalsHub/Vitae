"""Regression: warm /jobs must not rescore the whole catalogue."""

from __future__ import annotations

from unittest.mock import patch

from app.db import SessionLocal
from app.models import JobListing, ListingMatchScore, ListingVisibility
from app.services import (
    list_job_cards,
    new_listing_public_id,
    refresh_profile_match_scores,
)
from app.accounts import ensure_account, get_active_profile
from app.models import User


def _seed_listings(n: int = 12, *, prefix: str = "perf") -> list[int]:
    db = SessionLocal()
    ids: list[int] = []
    try:
        for i in range(n):
            listing = JobListing(
                public_id=new_listing_public_id(),
                source="test",
                external_id=f"{prefix}-{i}-{new_listing_public_id()[:8]}",
                title="Backend Engineer TypeScript Node.js",
                company=f"Co {i}",
                location="Remote",
                url=f"https://example.com/jobs/{prefix}-{i}",
                description=(
                    "We need a TypeScript Node.js backend engineer with NestJS, "
                    "PostgreSQL, Redis, Docker, and AWS experience."
                ),
                visibility=ListingVisibility.PUBLIC.value,
                scope_key="public",
                is_active=True,
            )
            db.add(listing)
            db.flush()
            ids.append(listing.id)
        db.commit()
        return ids
    finally:
        db.close()


def test_warm_list_job_cards_skips_rescoring(confirmed_user):
    _seed_listings(10, prefix="warm")
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == confirmed_user["email"]).one()
        ensure_account(db, user)
        # Cold fill
        n = refresh_profile_match_scores(db, user)
        db.commit()
        assert n >= 10
        active = get_active_profile(db, user)
        assert (
            db.query(ListingMatchScore)
            .filter(ListingMatchScore.profile_id == active.id)
            .count()
            >= 10
        )

        # Warm browse must not call score_job for each listing again.
        with patch("app.matching.score_cache.score_job") as mocked:
            mocked.side_effect = AssertionError("score_job should not run on warm cache")
            cards = list_job_cards(db, user, min_score=0)
            assert len(cards) >= 10
            assert mocked.call_count == 0
    finally:
        db.close()


def test_jobs_board_second_load_ok(confirmed_user, client):
    _seed_listings(8, prefix="board")
    r1 = client.get("/jobs")
    assert r1.status_code == 200
    with patch("app.matching.score_cache.score_job") as mocked:
        mocked.side_effect = AssertionError("warm /jobs must use cached scores")
        r2 = client.get("/jobs")
        assert r2.status_code == 200
        assert mocked.call_count == 0
