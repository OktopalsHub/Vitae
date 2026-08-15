"""Persisted profile×listing match scores for fast /jobs browse."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.matching.scorer import reasons_to_json, score_job
from app.models import ListingMatchScore

log = logging.getLogger(__name__)

# Cap JD text used for ranking (detail pages still load full description).
RANK_DESC_CHARS = 6000


def score_fingerprint(profile: dict[str, Any], cfg: dict[str, Any]) -> str:
    """Hash profile + scoring-relevant settings so stale cache rows are detectable."""
    payload = {
        "skills": profile.get("skills") or cfg.get("profile_skills") or [],
        "summary": (profile.get("summary") or "")[:800],
        "search": cfg.get("search") or {},
        "profile_skills": cfg.get("profile_skills") or [],
        "title_keywords": cfg.get("title_keywords") or [],
        "penalty_keywords": cfg.get("penalty_keywords") or [],
        "exclude_title_patterns": cfg.get("exclude_title_patterns") or [],
    }
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def ranking_payload(listing: Any) -> dict[str, Any]:
    desc = listing.description or ""
    if len(desc) > RANK_DESC_CHARS:
        desc = desc[:RANK_DESC_CHARS]
    return {
        "title": listing.title,
        "company": listing.company,
        "location": listing.location,
        "url": listing.url,
        "description": desc,
        "salary": listing.salary,
    }


def score_listing_cached(
    listing: Any, profile: dict[str, Any], cfg: dict[str, Any]
) -> tuple[float, str]:
    score, reasons = score_job(ranking_payload(listing), profile, cfg)
    return float(score), reasons_to_json(reasons)


def upsert_match_score(
    db: Session,
    *,
    profile_id: int,
    listing_id: int,
    match_score: float,
    match_reasons: str,
    fingerprint: str,
    existing: ListingMatchScore | None = None,
) -> ListingMatchScore:
    row = existing
    if row is None:
        row = (
            db.query(ListingMatchScore)
            .filter(
                ListingMatchScore.profile_id == profile_id,
                ListingMatchScore.listing_id == listing_id,
            )
            .one_or_none()
        )
    now = datetime.utcnow()
    if row is None:
        row = ListingMatchScore(
            profile_id=profile_id,
            listing_id=listing_id,
            match_score=match_score,
            match_reasons=match_reasons or "",
            fingerprint=fingerprint,
            scored_at=now,
        )
        db.add(row)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            log.debug(
                "Race on (profile_id=%s, listing_id=%s) — falling back to update",
                profile_id,
                listing_id,
            )
            row = (
                db.query(ListingMatchScore)
                .filter(
                    ListingMatchScore.profile_id == profile_id,
                    ListingMatchScore.listing_id == listing_id,
                )
                .one()
            )
            row.match_score = match_score
            row.match_reasons = match_reasons or ""
            row.fingerprint = fingerprint
            row.scored_at = now
            db.flush()
    else:
        row.match_score = match_score
        row.match_reasons = match_reasons or ""
        row.fingerprint = fingerprint
        row.scored_at = now
    return row


def load_all_scores_for_profile(db: Session, profile_id: int) -> dict[int, ListingMatchScore]:
    """All score rows for a profile (any fingerprint) — for bulk refresh."""
    rows = (
        db.query(ListingMatchScore)
        .filter(ListingMatchScore.profile_id == profile_id)
        .all()
    )
    return {r.listing_id: r for r in rows}


def load_scores_for_profile(
    db: Session, profile_id: int, fingerprint: str
) -> dict[int, ListingMatchScore]:
    rows = (
        db.query(ListingMatchScore)
        .filter(
            ListingMatchScore.profile_id == profile_id,
            ListingMatchScore.fingerprint == fingerprint,
        )
        .all()
    )
    return {r.listing_id: r for r in rows}
