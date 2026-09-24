from datetime import datetime, timedelta

from app.models import JobListing, JobSource, JobSourceRecord
from app.services import (
    _upsert_source,
    _upsert_source_record,
    close_missing_public_listings,
    upsert_public_listing,
)
from app.sources.base import RawJob


def test_job_catalogue_persists_normalized_fields_and_source_provenance(db_session):
    raw = RawJob(
        source="remoteok",
        external_id="remote-123",
        title="Senior Backend Engineer",
        company="Acme",
        location="Lagos, Nigeria",
        url="https://example.com/jobs/remote-123?utm_source=x",
        description="Build APIs",
        salary="$120000-$140000",
        normalized_location="Lagos",
        employment_type="full_time",
        remote_type="remote",
        experience_level="senior",
        salary_min=120000,
        salary_max=140000,
        salary_currency="USD",
        posted_at=datetime.utcnow(),
    )

    listing = upsert_public_listing(db_session, raw, source_key="remoteok")
    source = _upsert_source(db_session, "remoteok")
    record = _upsert_source_record(db_session, source, listing, raw)
    db_session.commit()

    assert listing.normalized_location == "Lagos"
    assert listing.remote_type == "remote"
    assert listing.salary_min == 120000
    assert listing.salary_max == 140000
    assert record.source_id == source.id
    assert record.listing_id == listing.id
    assert db_session.query(JobSource).filter_by(key="remoteok").count() == 1
    assert db_session.query(JobSourceRecord).filter_by(external_id="remote-123").count() == 1


def test_successful_source_can_close_stale_listings_without_touching_failed_sources(db_session):
    now = datetime.utcnow()
    stale_ok = JobListing(
        public_id="stale-ok",
        source="remoteok",
        source_key="remoteok",
        external_id="1",
        title="Old job",
        scope_key="public",
        visibility="public",
        is_active=True,
        last_seen_at=now - timedelta(hours=2),
    )
    stale_failed = JobListing(
        public_id="stale-failed",
        source="adzuna",
        source_key="adzuna",
        external_id="2",
        title="Still live",
        scope_key="public",
        visibility="public",
        is_active=True,
        last_seen_at=now - timedelta(hours=2),
    )
    db_session.add_all([stale_ok, stale_failed])
    db_session.commit()

    closed = close_missing_public_listings(
        db_session,
        now - timedelta(hours=1),
        sources={"remoteok"},
    )
    db_session.commit()

    assert closed == 1
    assert db_session.get(JobListing, stale_ok.id).is_active is False
    assert db_session.get(JobListing, stale_failed.id).is_active is True
