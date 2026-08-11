from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import get_settings, load_yaml_config
from app.matching.scorer import reasons_to_json, score_job
from app.models import (
    JobCard,
    JobListing,
    JobStatus,
    ListingVisibility,
    User,
    UserJob,
)
from app.sources.base import RawJob
from app.sources.fetchers import dedupe_raw_jobs, ingest_pasted_job, iter_fetch_sources
from app.accounts import (
    ensure_account,
    get_active_profile,
    load_user_profile_dict,
    load_user_settings,
)
from app.scheduler_state import mark_sync_finished, mark_sync_progress, mark_sync_started


def _score_payload(listing: JobListing) -> dict[str, Any]:
    return {
        "title": listing.title,
        "company": listing.company,
        "location": listing.location,
        "url": listing.url,
        "description": listing.description,
        "salary": listing.salary,
    }


def score_listing(
    listing: JobListing, profile: dict[str, Any], cfg: dict[str, Any]
) -> tuple[float, str]:
    score, reasons = score_job(_score_payload(listing), profile, cfg)
    return float(score), reasons_to_json(reasons)


def job_card_from(
    listing: JobListing,
    overlay: UserJob | None,
    *,
    profile: dict[str, Any],
    cfg: dict[str, Any],
    prefer_cached: bool = True,
) -> JobCard:
    """Build a read model. Scores live unless a scored overlay exists."""
    if overlay is not None and prefer_cached and overlay.scored_at is not None:
        return JobCard(
            listing=listing,
            match_score=float(overlay.match_score or 0),
            match_reasons=overlay.match_reasons or "",
            status=overlay.status or JobStatus.NEW.value,
            output_dir=overlay.output_dir,
            user_job=overlay,
        )
    score, reasons = score_listing(listing, profile, cfg)
    return JobCard(
        listing=listing,
        match_score=score,
        match_reasons=reasons,
        status=(overlay.status if overlay else JobStatus.NEW.value),
        output_dir=overlay.output_dir if overlay else None,
        user_job=overlay,
    )


def upsert_public_listing(db: Session, raw: RawJob) -> JobListing:
    existing = (
        db.query(JobListing)
        .filter(
            JobListing.source == raw.source,
            JobListing.external_id == raw.external_id,
            JobListing.scope_key == "public",
        )
        .one_or_none()
    )
    now = datetime.utcnow()
    if existing:
        existing.title = raw.title
        existing.company = raw.company or existing.company
        existing.location = raw.location or existing.location
        existing.url = raw.url or existing.url
        existing.description = raw.description or existing.description
        existing.salary = raw.salary or existing.salary
        existing.visibility = ListingVisibility.PUBLIC.value
        existing.is_active = True
        existing.closed_at = None
        existing.last_seen_at = now
        return existing
    listing = JobListing(
        source=raw.source,
        external_id=raw.external_id,
        title=raw.title,
        company=raw.company,
        location=raw.location,
        url=raw.url,
        description=raw.description,
        salary=raw.salary,
        visibility=ListingVisibility.PUBLIC.value,
        scope_key="public",
        owner_user_id=None,
        is_active=True,
        closed_at=None,
        last_seen_at=now,
    )
    db.add(listing)
    db.flush()
    return listing


def close_missing_public_listings(db: Session, sync_started_at: datetime) -> int:
    """Mark public catalogue rows not refreshed in this sync as closed."""
    now = datetime.utcnow()
    rows = (
        db.query(JobListing)
        .filter(
            JobListing.scope_key == "public",
            JobListing.is_active.is_(True),
            JobListing.last_seen_at < sync_started_at,
        )
        .all()
    )
    for listing in rows:
        listing.is_active = False
        listing.closed_at = now
    return len(rows)


def visible_listings_query(db: Session, user: User, *, active_only: bool = True):
    q = db.query(JobListing).filter(
        or_(
            JobListing.visibility == ListingVisibility.PUBLIC.value,
            (JobListing.visibility == ListingVisibility.PRIVATE.value)
            & (JobListing.owner_user_id == user.id),
        )
    )
    if active_only:
        q = q.filter(JobListing.is_active.is_(True))
    return q


def get_overlay(db: Session, user: User, listing_id: int) -> UserJob | None:
    active = get_active_profile(db, user)
    return (
        db.query(UserJob)
        .filter(UserJob.profile_id == active.id, UserJob.listing_id == listing_id)
        .one_or_none()
    )


def ensure_user_job(
    db: Session,
    user: User,
    listing: JobListing,
    *,
    profile: dict[str, Any] | None = None,
    cfg: dict[str, Any] | None = None,
) -> UserJob:
    """Create the thin ranking/status row on first real interaction."""
    ensure_account(db, user)
    active = get_active_profile(db, user)
    row = get_overlay(db, user, listing.id)
    if row:
        if row.profile_id is None:
            row.profile_id = active.id
            db.add(row)
        return row
    profile = profile if profile is not None else load_user_profile_dict(db, user)
    cfg = cfg if cfg is not None else load_user_settings(db, user)
    score, reasons = score_listing(listing, profile, cfg)
    row = UserJob(
        user_id=user.id,
        profile_id=active.id,
        listing_id=listing.id,
        match_score=score,
        match_reasons=reasons,
        status=JobStatus.NEW.value,
        scored_at=datetime.utcnow(),
    )
    db.add(row)
    db.flush()
    return row


# Back-compat alias used in a few call sites
ensure_user_job_for_listing = ensure_user_job


def refresh_overlay_score(
    db: Session,
    user: User,
    listing: JobListing,
    overlay: UserJob,
    profile: dict[str, Any],
    cfg: dict[str, Any],
) -> UserJob:
    score, reasons = score_listing(listing, profile, cfg)
    overlay.match_score = score
    overlay.match_reasons = reasons
    overlay.scored_at = datetime.utcnow()
    return overlay


def list_job_cards(
    db: Session,
    user: User,
    *,
    status: str = "",
    source: str = "",
    min_score: float = 0,
    q: str = "",
) -> list[JobCard]:
    """Catalogue browse: live-score rows; never fan out UserJob inserts."""
    ensure_account(db, user)
    listings = visible_listings_query(db, user).all()
    active = get_active_profile(db, user)
    overlays = {
        uj.listing_id: uj
        for uj in db.query(UserJob)
        .filter(UserJob.profile_id == active.id)
        .all()
    }
    profile = load_user_profile_dict(db, user)
    cfg = load_user_settings(db, user)
    cards: list[JobCard] = []
    qn = q.lower().strip()
    for listing in listings:
        if source and listing.source.lower() != source.lower():
            continue
        if qn:
            blob = f"{listing.title} {listing.company} {listing.location}".lower()
            if qn not in blob:
                continue
        card = job_card_from(
            listing,
            overlays.get(listing.id),
            profile=profile,
            cfg=cfg,
            prefer_cached=True,
        )
        if card.match_score < min_score:
            continue
        if status and card.status != status:
            continue
        cards.append(card)
    cards.sort(key=lambda c: (-c.match_score, c.title.lower()))
    return cards


def rescore_user_jobs(db: Session, user: User) -> int:
    """Refresh scores only on existing overlays — does not materialize the catalogue."""
    ensure_account(db, user)
    profile = load_user_profile_dict(db, user)
    cfg = load_user_settings(db, user)
    rows = (
        db.query(UserJob)
        .filter(UserJob.user_id == user.id)
        .all()
    )
    n = 0
    for overlay in rows:
        listing = overlay.listing or db.get(JobListing, overlay.listing_id)
        if listing is None:
            continue
        refresh_overlay_score(db, user, listing, overlay, profile, cfg)
        n += 1
    db.commit()
    return n


async def sync_public_jobs(db: Session) -> dict[str, Any]:
    """Upsert jobs from APIs (commit per source); close listings missing at the end."""
    mark_sync_started()
    sync_started_at = datetime.utcnow()
    cfg = load_yaml_config()
    settings = get_settings()

    url_index: dict[str, JobListing] = {}
    for cand in db.query(JobListing).filter(
        JobListing.scope_key == "public",
        JobListing.url != "",
    ).all():
        key = (cand.url or "").split("?")[0].rstrip("/").lower()
        if key:
            url_index[key] = cand

    created = updated = reopened = fetched = 0
    any_batch = False
    try:
        async for label, batch in iter_fetch_sources(cfg, settings):
            if not batch:
                mark_sync_progress(
                    source=label, fetched=fetched, created=created, updated=updated
                )
                continue
            any_batch = True
            batch = dedupe_raw_jobs(list(batch))
            seen_in_batch: set[str] = set()
            for raw in batch:
                dkey = f"{raw.source}:{raw.external_id}"
                if dkey in seen_in_batch:
                    continue
                seen_in_batch.add(dkey)
                before = (
                    db.query(JobListing)
                    .filter(
                        JobListing.source == raw.source,
                        JobListing.external_id == raw.external_id,
                        JobListing.scope_key == "public",
                    )
                    .one_or_none()
                )
                was_closed = bool(before and not before.is_active)
                canon = (raw.url or "").split("?")[0].rstrip("/").lower()
                existing_by_url = url_index.get(canon) if canon else None
                if existing_by_url and not before:
                    was_closed = not existing_by_url.is_active
                    existing_by_url.title = raw.title
                    existing_by_url.company = raw.company or existing_by_url.company
                    existing_by_url.location = raw.location or existing_by_url.location
                    existing_by_url.description = (
                        raw.description or existing_by_url.description
                    )
                    existing_by_url.salary = raw.salary or existing_by_url.salary
                    existing_by_url.is_active = True
                    existing_by_url.closed_at = None
                    existing_by_url.last_seen_at = datetime.utcnow()
                    updated += 1
                    if was_closed:
                        reopened += 1
                    continue

                listing = upsert_public_listing(db, raw)
                if before:
                    updated += 1
                    if was_closed:
                        reopened += 1
                else:
                    created += 1
                    if canon:
                        url_index[canon] = listing

            fetched += len(batch)
            db.commit()
            mark_sync_progress(
                source=label,
                fetched=fetched,
                created=created,
                updated=updated,
            )

        closed = 0
        if any_batch:
            closed = close_missing_public_listings(db, sync_started_at)
            db.commit()
        result = {
            "fetched": fetched,
            "created": created,
            "updated": updated,
            "reopened": reopened,
            "closed": closed,
            "rescored": 0,
        }
        mark_sync_finished(ok=True, result=result)
        return result
    except Exception as exc:
        db.rollback()
        mark_sync_finished(ok=False, error=str(exc))
        raise



async def add_pasted_job_for_user(
    db: Session,
    user: User,
    *,
    title: str = "",
    company: str = "",
    location: str = "",
    url: str = "",
    description: str = "",
) -> JobCard:
    ensure_account(db, user)
    cfg = load_user_settings(db, user)
    profile = load_user_profile_dict(db, user)
    raw = await ingest_pasted_job(
        title=title,
        company=company,
        location=location,
        url=url,
        description=description,
    )
    listing = JobListing(
        source="paste",
        external_id=raw.external_id,
        title=raw.title,
        company=raw.company,
        location=raw.location,
        url=raw.url,
        description=raw.description,
        salary=raw.salary,
        visibility=ListingVisibility.PRIVATE.value,
        scope_key=str(user.id),
        owner_user_id=user.id,
        is_active=True,
        last_seen_at=datetime.utcnow(),
    )
    db.add(listing)
    db.flush()
    overlay = ensure_user_job(db, user, listing, profile=profile, cfg=cfg)
    db.commit()
    db.refresh(listing)
    db.refresh(overlay)
    return job_card_from(listing, overlay, profile=profile, cfg=cfg)


def get_user_job_card(db: Session, user: User, listing_id: int) -> JobCard | None:
    """Open/interaction path: creates overlay so status/CV can attach."""
    listing = db.get(JobListing, listing_id)
    if not listing:
        return None
    if listing.visibility == ListingVisibility.PRIVATE.value:
        if listing.owner_user_id != user.id:
            return None
    elif listing.visibility != ListingVisibility.PUBLIC.value:
        return None
    elif not listing.is_active:
        return None
    profile = load_user_profile_dict(db, user)
    cfg = load_user_settings(db, user)
    overlay = ensure_user_job(db, user, listing, profile=profile, cfg=cfg)
    return job_card_from(listing, overlay, profile=profile, cfg=cfg)


async def sync_jobs(db: Session) -> dict[str, Any]:
    return await sync_public_jobs(db)


def rescore_all_jobs(db: Session) -> int:
    """Deprecated: platform-wide user×listing rescoring is intentionally gone."""
    return 0
