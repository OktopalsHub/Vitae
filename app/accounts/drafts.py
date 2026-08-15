from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.accounts.profile import get_active_profile
from app.models import ApplyDraft, JobListing, ListingVisibility, User


def _assert_listing_accessible(db: Session, user: User, listing_id: int) -> None:
    """Raise PermissionError if the user cannot access this listing."""
    listing = db.get(JobListing, listing_id)
    if listing is None:
        raise PermissionError("Listing not found")
    if listing.visibility == ListingVisibility.PRIVATE.value:
        if listing.owner_user_id != user.id:
            raise PermissionError("Access denied")
    elif listing.visibility != ListingVisibility.PUBLIC.value:
        raise PermissionError("Access denied")


def load_apply_draft_db(db: Session, user: User, listing_id: int) -> dict[str, Any]:
    _assert_listing_accessible(db, user, listing_id)
    active = get_active_profile(db, user)
    row = (
        db.query(ApplyDraft)
        .filter(
            ApplyDraft.profile_id == active.id,
            ApplyDraft.listing_id == listing_id,
        )
        .one_or_none()
    )
    if row:
        try:
            answers = json.loads(row.answers_json or "[]")
        except json.JSONDecodeError:
            answers = []
        return {
            "cover_blurb": row.cover_blurb or "",
            "answers": answers if isinstance(answers, list) else [],
        }
    return {}


def save_apply_draft_db(
    db: Session, user: User, listing_id: int, data: dict[str, Any]
) -> None:
    _assert_listing_accessible(db, user, listing_id)
    active = get_active_profile(db, user)
    row = (
        db.query(ApplyDraft)
        .filter(
            ApplyDraft.profile_id == active.id,
            ApplyDraft.listing_id == listing_id,
        )
        .one_or_none()
    )
    answers = data.get("answers") if isinstance(data.get("answers"), list) else []
    cover = str(data.get("cover_blurb") or "")
    if row:
        if "cover_blurb" in data:
            row.cover_blurb = cover
        if "answers" in data:
            row.answers_json = json.dumps(answers)
        row.user_id = user.id
        db.add(row)
    else:
        db.add(
            ApplyDraft(
                user_id=user.id,
                profile_id=active.id,
                listing_id=listing_id,
                cover_blurb=cover,
                answers_json=json.dumps(answers),
            )
        )
    db.flush()
