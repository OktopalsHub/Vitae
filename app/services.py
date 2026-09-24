from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import datetime
from typing import Any

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, load_only, noload

log = logging.getLogger(__name__)

from app.config import get_settings, load_yaml_config
from app.matching.score_cache import (
    load_all_scores_for_profile,
    load_scores_for_profile,
    score_fingerprint,
    score_listing_cached,
    upsert_match_score,
)
from app.db import SessionLocal
from app.matching.scorer import MATCHING_ALGORITHM_VERSION, reasons_to_json, score_job_versioned
from app.models import (
    JobCard,
    JobListing,
    JobSource,
    JobSourceRecord,
    JobStatus,
    ListingMatchScore,
    ListingVisibility,
    User,
    UserJob,
)
from app.sources.base import RawJob
from app.sources.fetchers import dedupe_raw_jobs, ingest_pasted_job
from app.sources.orchestrator import iter_fetch_source_results
from app.accounts import (
    ensure_account,
    get_active_profile,
    load_user_profile_dict,
    load_user_settings,
)
from app.scheduler_state import mark_sync_finished, mark_sync_progress, mark_sync_started


def _canonical_key(raw: RawJob) -> str:
    """Stable cross-source identity without storing long or sensitive source URLs."""
    url = (raw.url or "").split("?")[0].rstrip("/").lower()
    if url:
        value = f"url:{url}"
    else:
        value = f"job:{raw.title.strip().lower()}|{raw.company.strip().lower()}|{raw.location.strip().lower()}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def new_listing_public_id() -> str:
    """Unguessable URL token (~22 url-safe chars)."""
    return secrets.token_urlsafe(16)[:22]


def ensure_listing_public_id(listing: JobListing) -> str:
    if (listing.public_id or "").strip():
        return listing.public_id
    listing.public_id = new_listing_public_id()
    return listing.public_id


def job_path(job: JobCard | JobListing | str) -> str:
    if isinstance(job, str):
        ref = job
    else:
        ref = getattr(job, "public_id", None) or ""
        if not ref and hasattr(job, "listing"):
            ref = getattr(job.listing, "public_id", "") or ""
    if not ref:
        raise ValueError("Job has no public_id")
    return f"/jobs/{ref}"


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
) -> tuple[float, str, str]:
    result = score_job_versioned(_score_payload(listing), profile, cfg)
    return float(result["score"]), reasons_to_json(result["reasons"]), json.dumps(result, ensure_ascii=False)


def job_card_from(
    listing: JobListing,
    overlay: UserJob | None,
    *,
    profile: dict[str, Any],
    cfg: dict[str, Any],
    prefer_cached: bool = True,
    cached_score: float | None = None,
    cached_reasons: str | None = None,
) -> JobCard:
    """Build a read model. Prefers ListingMatchScore / overlay over live scoring."""
    if cached_score is not None:
        return JobCard(
            listing=listing,
            match_score=float(cached_score),
            match_reasons=cached_reasons or "",
            status=(overlay.status if overlay else JobStatus.NEW.value),
            output_dir=overlay.output_dir if overlay else None,
            user_job=overlay,
        )
    if overlay is not None and prefer_cached and overlay.scored_at is not None:
        return JobCard(
            listing=listing,
            match_score=float(overlay.match_score or 0),
            match_reasons=overlay.match_reasons or "",
            status=overlay.status or JobStatus.NEW.value,
            output_dir=overlay.output_dir,
            user_job=overlay,
        )
    score, reasons = score_listing_cached(listing, profile, cfg)
    return JobCard(
        listing=listing,
        match_score=score,
        match_reasons=reasons,
        status=(overlay.status if overlay else JobStatus.NEW.value),
        output_dir=overlay.output_dir if overlay else None,
        user_job=overlay,
    )


def _source_display_name(source_key: str) -> str:
    return source_key.replace(":", " / ").replace("_", " ").title()


def _upsert_source(db: Session, key: str) -> JobSource:
    row = db.query(JobSource).filter(JobSource.key == key).one_or_none()
    if row is None:
        row = JobSource(key=key, display_name=_source_display_name(key), enabled=True)
        db.add(row)
        db.flush()
    return row


def _upsert_source_record(db: Session, source: JobSource, listing: JobListing, raw: RawJob) -> JobSourceRecord:
    now = datetime.utcnow()
    row = db.query(JobSourceRecord).filter(
        JobSourceRecord.source_id == source.id,
        JobSourceRecord.external_id == raw.external_id,
    ).one_or_none()
    payload = json.dumps(raw.to_dict(), default=str, ensure_ascii=False)
    if row is None:
        row = JobSourceRecord(
            source_id=source.id,
            listing_id=listing.id,
            external_id=raw.external_id,
            payload_json=payload,
            first_seen_at=now,
            last_seen_at=now,
            is_active=True,
        )
        db.add(row)
    else:
        row.listing_id = listing.id
        row.payload_json = payload
        row.last_seen_at = now
        row.is_active = True
    return row


def upsert_public_listing(db: Session, raw: RawJob, *, source_key: str | None = None) -> JobListing:
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
        existing.source_key = source_key or existing.source_key or raw.source
        existing.normalized_location = raw.normalized_location or raw.location or existing.normalized_location
        existing.employment_type = raw.employment_type or existing.employment_type
        existing.remote_type = raw.remote_type or existing.remote_type
        existing.experience_level = raw.experience_level or existing.experience_level
        existing.salary_min = raw.salary_min if raw.salary_min is not None else existing.salary_min
        existing.salary_max = raw.salary_max if raw.salary_max is not None else existing.salary_max
        existing.salary_currency = raw.salary_currency or existing.salary_currency
        existing.posted_at = raw.posted_at or existing.posted_at
        existing.expires_at = raw.expires_at or existing.expires_at
        existing.source_updated_at = raw.source_updated_at or existing.source_updated_at
        existing.canonical_key = existing.canonical_key or _canonical_key(raw)
        existing.visibility = ListingVisibility.PUBLIC.value
        existing.is_active = True
        existing.closed_at = None
        existing.last_seen_at = now
        ensure_listing_public_id(existing)
        return existing
    listing = JobListing(
        public_id=new_listing_public_id(),
        source=raw.source,
        external_id=raw.external_id,
        title=raw.title,
        company=raw.company,
        location=raw.location,
        url=raw.url,
        description=raw.description,
        salary=raw.salary,
        canonical_key=(raw.url or raw.external_id).split("?")[0].rstrip("/").lower()[:128],
        source_key=source_key or raw.source,
        normalized_location=raw.normalized_location or raw.location,
        employment_type=raw.employment_type,
        remote_type=raw.remote_type,
        experience_level=raw.experience_level,
        salary_min=raw.salary_min,
        salary_max=raw.salary_max,
        salary_currency=raw.salary_currency,
        posted_at=raw.posted_at,
        expires_at=raw.expires_at,
        source_updated_at=raw.source_updated_at,
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


def close_missing_public_listings(
    db: Session,
    sync_started_at: datetime,
    *,
    sources: set[str] | None = None,
) -> int:
    """Mark public catalogue rows not refreshed in this sync as closed.

    When ``sources`` is provided, only listings from those sources are closed.
    This avoids deactivating jobs from boards that returned an empty/failed batch.
    """
    now = datetime.utcnow()
    q = db.query(JobListing).filter(
        JobListing.scope_key == "public",
        JobListing.is_active.is_(True),
        JobListing.last_seen_at < sync_started_at,
    )
    if sources is not None:
        if not sources:
            return 0
        q = q.filter(JobListing.source_key.in_(sources))
    rows = q.all()
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
    score, reasons, _breakdown = score_listing(listing, profile, cfg)
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
    """Catalogue browse using persisted ListingMatchScore (lazy-fill misses)."""
    ensure_account(db, user)
    active = get_active_profile(db, user)
    profile = load_user_profile_dict(db, user)
    cfg = load_user_settings(db, user)
    fingerprint = score_fingerprint(profile, cfg)
    score_map = load_scores_for_profile(db, active.id, fingerprint)

    listings = (
        visible_listings_query(db, user)
        .options(
            load_only(
                JobListing.id,
                JobListing.public_id,
                JobListing.source,
                JobListing.external_id,
                JobListing.title,
                JobListing.company,
                JobListing.location,
                JobListing.url,
                JobListing.description,
                JobListing.salary,
                JobListing.visibility,
                JobListing.scope_key,
                JobListing.owner_user_id,
                JobListing.is_active,
            )
        )
        .all()
    )
    # Fully cold fingerprint: score once with a single existing-row lookup (not N SELECTs).
    existing_rows: dict[int, ListingMatchScore] | None = None
    if listings and not score_map:
        existing_rows = load_all_scores_for_profile(db, active.id)
        for i, listing in enumerate(listings, start=1):
            score, reasons, breakdown = score_listing_cached(listing, profile, cfg)
            row = upsert_match_score(
                db,
                profile_id=active.id,
                listing_id=listing.id,
                match_score=score,
                match_reasons=reasons,
                breakdown_json=breakdown,
                algorithm_version=MATCHING_ALGORITHM_VERSION,
                fingerprint=fingerprint,
                existing=existing_rows.get(listing.id),
            )
            score_map[listing.id] = row
            if i % 200 == 0:
                try:
                    db.flush()
                except IntegrityError:
                    db.rollback()
                    log.debug("Flush batch failed due to race — continuing")
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            log.debug("Final cold-path flush failed due to race — continuing")

    overlays = {
        uj.listing_id: uj
        for uj in db.query(UserJob)
        .options(
            noload(UserJob.listing),
            load_only(
                UserJob.id,
                UserJob.listing_id,
                UserJob.match_score,
                UserJob.match_reasons,
                UserJob.status,
                UserJob.output_dir,
                UserJob.scored_at,
                UserJob.profile_id,
                UserJob.user_id,
            ),
        )
        .filter(UserJob.profile_id == active.id)
        .all()
    }

    qn = q.lower().strip()
    cards: list[JobCard] = []
    for listing in listings:
        ensure_listing_public_id(listing)
        if source and listing.source.lower() != source.lower():
            continue
        if qn:
            blob = f"{listing.title} {listing.company} {listing.location}".lower()
            if qn not in blob:
                continue

        overlay = overlays.get(listing.id)
        cached = score_map.get(listing.id)
        if cached is not None:
            score = float(cached.match_score or 0)
            reasons = cached.match_reasons or ""
        else:
            if existing_rows is None:
                existing_rows = load_all_scores_for_profile(db, active.id)
            score, reasons, breakdown = score_listing_cached(listing, profile, cfg)
            upsert_match_score(
                db,
                profile_id=active.id,
                listing_id=listing.id,
                match_score=score,
                match_reasons=reasons,
                fingerprint=fingerprint,
                existing=existing_rows.get(listing.id),
            )

        if score < min_score:
            continue
        status_val = (overlay.status if overlay else JobStatus.NEW.value) or JobStatus.NEW.value
        if status and status_val != status:
            continue
        cards.append(
            JobCard(
                listing=listing,
                match_score=score,
                match_reasons=reasons,
                status=status_val,
                output_dir=overlay.output_dir if overlay else None,
                user_job=overlay,
            )
        )

    cards.sort(key=lambda c: (-c.match_score, c.title.lower()))
    try:
        db.flush()  # persist score fills + any newly assigned public_id tokens
    except IntegrityError:
        db.rollback()
        log.debug("Final flush in list_job_cards failed due to race — continuing")
    return cards


def refresh_profile_match_scores(db: Session, user: User) -> int:
    """Recompute ListingMatchScore for all visible listings on the active profile."""
    ensure_account(db, user)
    active = get_active_profile(db, user)
    profile = load_user_profile_dict(db, user)
    cfg = load_user_settings(db, user)
    fingerprint = score_fingerprint(profile, cfg)
    listings = visible_listings_query(db, user).options(
        load_only(
            JobListing.id,
            JobListing.title,
            JobListing.company,
            JobListing.location,
            JobListing.url,
            JobListing.description,
            JobListing.salary,
        )
    ).all()
    existing = load_all_scores_for_profile(db, active.id)
    n = 0
    for listing in listings:
        score, reasons, breakdown = score_listing_cached(listing, profile, cfg)
        upsert_match_score(
            db,
            profile_id=active.id,
            listing_id=listing.id,
            match_score=score,
            match_reasons=reasons,
            breakdown_json=breakdown,
            algorithm_version=MATCHING_ALGORITHM_VERSION,
            fingerprint=fingerprint,
            existing=existing.get(listing.id),
        )
        n += 1
        if n % 200 == 0:
            try:
                db.flush()
            except IntegrityError:
                db.rollback()
                log.debug("Flush batch in refresh_profile_match_scores failed due to race")
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        log.debug("Final flush in refresh_profile_match_scores failed due to race")
    return n


def rescore_user_jobs(db: Session, user: User) -> int:
    """Refresh persisted match scores + any existing overlays for this user."""
    ensure_account(db, user)
    profile = load_user_profile_dict(db, user)
    cfg = load_user_settings(db, user)
    n = refresh_profile_match_scores(db, user)
    rows = (
        db.query(UserJob)
        .options(noload(UserJob.listing))
        .filter(UserJob.user_id == user.id)
        .all()
    )
    listing_ids = [o.listing_id for o in rows if o.listing_id]
    listings_by_id = {
        L.id: L
        for L in db.query(JobListing).filter(JobListing.id.in_(listing_ids)).all()
    } if listing_ids else {}
    for overlay in rows:
        listing = listings_by_id.get(overlay.listing_id)
        if listing is None:
            continue
        refresh_overlay_score(db, user, listing, overlay, profile, cfg)
    db.commit()
    return n


def rescore_user_jobs_background(user_id: Any) -> None:
    """Own-session wrapper for FastAPI BackgroundTasks (do not block the request)."""
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if user is None:
            return
        rescore_user_jobs(db, user)
    except Exception:  # noqa: BLE001 — background must not crash the worker
        db.rollback()
        raise
    finally:
        db.close()


def top_clear_listing_ids(
    db: Session,
    user: User,
    *,
    limit: int,
    min_score: float | None = None,
) -> list[int]:
    """Top matched listing IDs from the score cache (no full live rescore)."""
    ensure_account(db, user)
    active = get_active_profile(db, user)
    profile = load_user_profile_dict(db, user)
    cfg = load_user_settings(db, user)
    threshold = (
        float(min_score)
        if min_score is not None
        else float((cfg.get("search") or {}).get("min_match_score") or 65)
    )
    fingerprint = score_fingerprint(profile, cfg)

    # Ensure cache is warm enough for top-N without scoring every row when populated.
    existing = (
        db.query(ListingMatchScore.listing_id)
        .filter(
            ListingMatchScore.profile_id == active.id,
            ListingMatchScore.fingerprint == fingerprint,
        )
        .limit(1)
        .first()
    )
    if existing is None:
        # Cold profile: fill cache once (same cost as one old /jobs load).
        refresh_profile_match_scores(db, user)
        db.commit()

    q = (
        db.query(ListingMatchScore.listing_id)
        .join(JobListing, JobListing.id == ListingMatchScore.listing_id)
        .filter(
            ListingMatchScore.profile_id == active.id,
            ListingMatchScore.fingerprint == fingerprint,
            ListingMatchScore.match_score >= threshold,
            JobListing.is_active.is_(True),
            or_(
                JobListing.visibility == ListingVisibility.PUBLIC.value,
                (JobListing.visibility == ListingVisibility.PRIVATE.value)
                & (JobListing.owner_user_id == user.id),
            ),
        )
        .order_by(ListingMatchScore.match_score.desc(), JobListing.title.asc())
        .limit(max(1, int(limit)))
    )
    return [int(row[0]) for row in q.all()]


async def sync_public_jobs(db: Session) -> dict[str, Any]:
    """Sync all configured sources with durable health/provenance and safe closure semantics."""
    mark_sync_started()
    sync_started_at = datetime.utcnow()
    cfg = load_yaml_config()
    settings = get_settings()

    url_index: dict[str, JobListing] = {}
    for cand in db.query(JobListing).filter(JobListing.scope_key == "public", JobListing.url != "").all():
        key = (cand.url or "").split("?")[0].rstrip("/").lower()
        if key:
            url_index[key] = cand

    created = updated = reopened = fetched = failed = 0
    successful_sources: set[str] = set()
    source_results: dict[str, dict[str, Any]] = {}
    try:
        async for label, ok, batch, error in iter_fetch_source_results(cfg, settings):
            source_row = _upsert_source(db, label)
            source_row.last_started_at = datetime.utcnow()
            if not ok:
                failed += 1
                source_row.last_error_at = datetime.utcnow()
                source_row.last_error = error[:4000]
                source_row.consecutive_failures += 1
                source_results[label] = {"ok": False, "error": error}
                db.commit()
                mark_sync_progress(source=label, fetched=fetched, created=created, updated=updated)
                continue

            successful_sources.add(label)
            source_row.last_success_at = datetime.utcnow()
            source_row.last_error = ""
            source_row.consecutive_failures = 0
            source_row.last_fetched_count = len(batch)
            batch = dedupe_raw_jobs(list(batch))
            seen_in_batch: set[str] = set()
            for raw in batch:
                dkey = f"{raw.source}:{raw.external_id}"
                if dkey in seen_in_batch:
                    continue
                seen_in_batch.add(dkey)
                before = db.query(JobListing).filter(
                    JobListing.source == raw.source,
                    JobListing.external_id == raw.external_id,
                    JobListing.scope_key == "public",
                ).one_or_none()
                was_closed = bool(before and not before.is_active)
                canon = (raw.url or "").split("?")[0].rstrip("/").lower()
                existing_by_url = url_index.get(canon) if canon else None
                if existing_by_url and not before:
                    listing = existing_by_url
                    was_closed = not listing.is_active
                    listing.title = raw.title or listing.title
                    listing.company = raw.company or listing.company
                    listing.location = raw.location or listing.location
                    listing.description = raw.description or listing.description
                    listing.salary = raw.salary or listing.salary
                    listing.normalized_location = raw.normalized_location or raw.location or listing.normalized_location
                    listing.employment_type = raw.employment_type or listing.employment_type
                    listing.remote_type = raw.remote_type or listing.remote_type
                    listing.experience_level = raw.experience_level or listing.experience_level
                    listing.salary_min = raw.salary_min if raw.salary_min is not None else listing.salary_min
                    listing.salary_max = raw.salary_max if raw.salary_max is not None else listing.salary_max
                    listing.salary_currency = raw.salary_currency or listing.salary_currency
                    listing.posted_at = raw.posted_at or listing.posted_at
                    listing.expires_at = raw.expires_at or listing.expires_at
                    listing.source_updated_at = raw.source_updated_at or listing.source_updated_at
                    listing.is_active = True
                    listing.closed_at = None
                    listing.last_seen_at = datetime.utcnow()
                    updated += 1
                    if was_closed:
                        reopened += 1
                else:
                    listing = upsert_public_listing(db, raw, source_key=label)
                    if before:
                        updated += 1
                        if was_closed:
                            reopened += 1
                    else:
                        created += 1
                        if canon:
                            url_index[canon] = listing
                db.flush()
                _upsert_source_record(db, source_row, listing, raw)

            fetched += len(batch)
            source_results[label] = {"ok": True, "fetched": len(batch)}
            db.commit()
            mark_sync_progress(source=label, fetched=fetched, created=created, updated=updated)

        closed = close_missing_public_listings(db, sync_started_at, sources=successful_sources)
        db.commit()
        result = {
            "fetched": fetched,
            "created": created,
            "updated": updated,
            "reopened": reopened,
            "closed": closed,
            "failed_sources": failed,
            "sources": source_results,
            "rescored": 0,
        }
        mark_sync_finished(ok=failed == 0, result=result, error="One or more sources failed" if failed else "")
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
        public_id=new_listing_public_id(),
        source="paste",
        external_id=raw.external_id,
        title=raw.title,
        company=raw.company,
        location=raw.location,
        url=raw.url,
        description=raw.description,
        salary=raw.salary,
        canonical_key=(raw.url or raw.external_id).split("?")[0].rstrip("/").lower()[:128],
        source_key="paste",
        normalized_location=raw.normalized_location or raw.location,
        employment_type=raw.employment_type,
        remote_type=raw.remote_type,
        experience_level=raw.experience_level,
        salary_min=raw.salary_min,
        salary_max=raw.salary_max,
        salary_currency=raw.salary_currency,
        posted_at=raw.posted_at,
        expires_at=raw.expires_at,
        source_updated_at=raw.source_updated_at,
        visibility=ListingVisibility.PRIVATE.value,
        scope_key=str(user.id),
        owner_user_id=user.id,
        is_active=True,
        last_seen_at=datetime.utcnow(),
    )
    db.add(listing)
    db.flush()
    overlay = ensure_user_job(db, user, listing, profile=profile, cfg=cfg)
    score, reasons = score_listing_cached(listing, profile, cfg)
    upsert_match_score(
        db,
        profile_id=get_active_profile(db, user).id,
        listing_id=listing.id,
        match_score=score,
        match_reasons=reasons,
        fingerprint=score_fingerprint(profile, cfg),
    )
    db.commit()
    db.refresh(listing)
    db.refresh(overlay)
    return job_card_from(
        listing,
        overlay,
        profile=profile,
        cfg=cfg,
        cached_score=score,
        cached_reasons=reasons,
    )


def _get_user_job_card_by_int_id(db: Session, user: User, listing_id: int) -> JobCard | None:
    """Internal-only: look up a card by sequential integer PK.

    DO NOT expose this via routes — integer IDs are guessable (IDOR).
    Use get_user_job_card_by_public_id for any user-facing path.
    """
    listing = db.get(JobListing, listing_id)
    if not listing:
        return None
    return _card_for_visible_listing(db, user, listing)


def get_user_job_card_by_public_id(db: Session, user: User, public_id: str) -> JobCard | None:
    ref = (public_id or "").strip()
    if not ref or ref.isdigit():
        # Reject sequential integer guessing — only opaque tokens are valid.
        return None
    listing = (
        db.query(JobListing)
        .filter(JobListing.public_id == ref)
        .one_or_none()
    )
    if not listing:
        return None
    return _card_for_visible_listing(db, user, listing)


def _card_for_visible_listing(
    db: Session, user: User, listing: JobListing
) -> JobCard | None:
    if listing.visibility == ListingVisibility.PRIVATE.value:
        if listing.owner_user_id != user.id:
            return None
    elif listing.visibility != ListingVisibility.PUBLIC.value:
        return None
    elif not listing.is_active:
        return None
    ensure_listing_public_id(listing)
    profile = load_user_profile_dict(db, user)
    cfg = load_user_settings(db, user)
    overlay = ensure_user_job(db, user, listing, profile=profile, cfg=cfg)
    return job_card_from(listing, overlay, profile=profile, cfg=cfg)

