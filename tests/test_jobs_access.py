from __future__ import annotations

from app.db import SessionLocal
from app.models import JobListing, ListingVisibility
from app.services import new_listing_public_id


def test_root_always_landing(client):
    r = client.get("/")
    assert r.status_code == 200
    assert b"landing-hero" in r.content or b"Vitae" in r.content
    assert b"job-list" not in r.content


def test_jobs_board_requires_login(client):
    r = client.get("/jobs", follow_redirects=False)
    assert r.status_code in {302, 303}
    assert "/login" in r.headers.get("location", "")


def test_job_detail_requires_login(client):
    r = client.get("/jobs/notarealtoken", follow_redirects=False)
    assert r.status_code in {302, 303}
    assert "/login" in r.headers.get("location", "")


def test_numeric_job_id_is_not_found(confirmed_user, client):
    db = SessionLocal()
    try:
        user = confirmed_user["user"]
        listing = JobListing(
            public_id=new_listing_public_id(),
            source="paste",
            external_id="access-1",
            title="Backend Engineer",
            company="Acme",
            location="Remote",
            url="https://example.com/job",
            description="Build APIs with TypeScript",
            visibility=ListingVisibility.PRIVATE.value,
            scope_key=str(user.id),
            owner_user_id=user.id,
            is_active=True,
        )
        db.add(listing)
        db.commit()
        db.refresh(listing)
        numeric_id = listing.id
        public_id = listing.public_id
    finally:
        db.close()

    r = client.get(f"/jobs/{numeric_id}", follow_redirects=False)
    assert r.status_code == 404

    r = client.get(f"/jobs/{public_id}", follow_redirects=False)
    assert r.status_code == 200
    assert b"Backend Engineer" in r.content


def test_profiles_page_lets_you_create_and_switch(confirmed_user, client):
    r = client.get("/profiles")
    assert r.status_code == 200
    assert b"Create profile" in r.content
    assert b'action="/profiles/create"' in r.content
    assert b">Profiles<" in r.content


def test_jobs_board_opens_in_same_tab(confirmed_user, client):
    r = client.get("/jobs")
    assert r.status_code == 200
    assert b'target="_blank"' not in r.content


def test_landing_shows_free_listings(client):
    r = client.get("/")
    assert r.status_code == 200
    assert b"First 3 listings free" in r.content
    assert b"Your first 3 listings are free" in r.content
    assert b"/ forever" not in r.content


def test_public_company_name_hides_job_sites():
    from app.models import public_company_name

    assert public_company_name("RemoteOK") == ""
    assert public_company_name("Wellfound startup") == ""
    assert public_company_name("ZipRecruiter") == ""
    assert public_company_name("remotive", "remotive") == ""
    assert public_company_name("Ashby") == ""
    assert public_company_name("BruntWork") == ""
    assert public_company_name("BruntWork", "bruntwork") == ""
    assert public_company_name("Acme Labs", "remoteok") == "Acme Labs"


def test_free_openings_are_one_time_per_profile(confirmed_user):
    from app.accounts.billing_access import (
        can_open_listing,
        free_opens_remaining,
        free_unlocked_listing_ids,
        FREE_CLEAR_MATCHES,
    )

    db = SessionLocal()
    try:
        user = confirmed_user["user"]
        listings = []
        for i in range(FREE_CLEAR_MATCHES + 1):
            listing = JobListing(
                public_id=new_listing_public_id(),
                source="paste",
                external_id=f"free-open-{i}",
                title=f"Role {i}",
                company="Acme",
                location="Remote",
                url=f"https://example.com/job-{i}",
                description="Build APIs with TypeScript",
                visibility=ListingVisibility.PUBLIC.value,
                scope_key="public",
                is_active=True,
            )
            db.add(listing)
            listings.append(listing)
        db.commit()
        for listing in listings:
            db.refresh(listing)

        assert free_opens_remaining(db, user) == FREE_CLEAR_MATCHES
        for listing in listings[:FREE_CLEAR_MATCHES]:
            ok, reason = can_open_listing(db, user, listing.id)
            assert ok, reason
        db.commit()
        unlocked = free_unlocked_listing_ids(db, user)
        assert unlocked == {listings[0].id, listings[1].id, listings[2].id}
        assert free_opens_remaining(db, user) == 0

        blocked, reason = can_open_listing(db, user, listings[3].id)
        assert not blocked
        assert "free openings" in reason.lower()

        # Re-open already claimed listing still works
        ok, _ = can_open_listing(db, user, listings[0].id)
        assert ok
        assert free_unlocked_listing_ids(db, user) == unlocked
    finally:
        db.close()


def test_free_board_blurs_until_opened(confirmed_user):
    from app.accounts.billing_access import (
        can_open_listing,
        listing_board_is_clear,
        listing_is_free_openable,
        FREE_CLEAR_MATCHES,
    )

    db = SessionLocal()
    try:
        user = confirmed_user["user"]
        listings = []
        for i in range(FREE_CLEAR_MATCHES + 2):
            listing = JobListing(
                public_id=new_listing_public_id(),
                source="paste",
                external_id=f"blur-board-{i}",
                title=f"Blur Role {i}",
                company="Acme",
                location="Remote",
                url=f"https://example.com/blur-{i}",
                description="Build APIs with TypeScript",
                visibility=ListingVisibility.PUBLIC.value,
                scope_key="public",
                is_active=True,
            )
            db.add(listing)
            listings.append(listing)
        db.commit()
        for listing in listings:
            db.refresh(listing)

        # Before any open: board is blurred, but still openable while free opens remain.
        assert not listing_board_is_clear(db, user, listings[0].id)
        assert listing_is_free_openable(db, user, listings[0].id)

        ok, _ = can_open_listing(db, user, listings[0].id)
        assert ok
        db.commit()
        assert listing_board_is_clear(db, user, listings[0].id)
        assert not listing_board_is_clear(db, user, listings[1].id)
    finally:
        db.close()


def test_landing_is_marketing_only(confirmed_user, client):
    """Signed-in session must not surface app chrome on /."""
    r = client.get("/")
    assert r.status_code == 200
    body = r.content
    assert b"landing-hero" in body
    assert b"job-list" not in body
    assert b"Log out" not in body
    assert b">Billing<" not in body
    assert b">Jobs<" not in body
    assert b">Settings<" not in body
    assert b"Get started" in body
    assert b"Sign in" in body
    assert b"hero-brand" not in body
    assert b"\xe2\x80\x94" not in body  # no em dash
