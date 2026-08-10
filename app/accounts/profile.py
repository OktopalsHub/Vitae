from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.accounts.bootstrap import ensure_account, ensure_profile_billing
from app.accounts.paths import profile_data_dir, user_data_dir
from app.accounts.settings import load_user_settings
from app.billing import PAID_PLANS
from app.models import Profile, ProfileBilling, User

__all__ = [
    "user_data_dir",
    "profile_data_dir",
    "get_active_profile",
    "list_user_profiles",
    "create_profile",
    "switch_active_profile",
    "rename_profile",
    "archive_profile",
    "get_profile_billing",
    "load_user_profile_dict",
    "apply_profile_from_user",
    "apply_profile_chips",
    "is_profile_confirmed",
]

_ACTIVE_SUB = frozenset({"active", "trialing"})


def _alive_profiles_q(db: Session, user: User):
    return (
        db.query(Profile)
        .filter(Profile.user_id == user.id, Profile.archived_at.is_(None))
        .order_by(Profile.id.asc())
    )


def get_active_profile(db: Session, user: User) -> Profile:
    """Return the user's active career profile (creates Default if needed)."""
    ensure_account(db, user)
    if user.active_profile_id:
        row = db.get(Profile, user.active_profile_id)
        if row is not None and row.user_id == user.id and row.archived_at is None:
            return row
    row = _alive_profiles_q(db, user).first()
    if row is None:
        row = (
            db.query(Profile)
            .filter(Profile.user_id == user.id)
            .order_by(Profile.id.asc())
            .first()
        )
        if row is not None:
            row.archived_at = None
            db.add(row)
            db.flush()
    assert row is not None
    if user.active_profile_id != row.id:
        user.active_profile_id = row.id
        db.add(user)
        db.flush()
    return row


def list_user_profiles(db: Session, user: User, *, include_archived: bool = False) -> list[Profile]:
    ensure_account(db, user)
    if include_archived:
        return (
            db.query(Profile)
            .filter(Profile.user_id == user.id)
            .order_by(Profile.archived_at.is_not(None), Profile.id.asc())
            .all()
        )
    return _alive_profiles_q(db, user).all()


def _slug_label(label: str) -> str:
    cleaned = re.sub(r"\s+", " ", (label or "").strip())
    return cleaned[:128] if cleaned else "Untitled"


def create_profile(
    db: Session,
    user: User,
    label: str,
    *,
    switch_to: bool = True,
) -> Profile:
    """Create a new career track with its own free billing row."""
    ensure_account(db, user)
    active = get_active_profile(db, user)
    profile = Profile(
        user_id=user.id,
        label=_slug_label(label),
        full_name=active.full_name or user.full_name or "",
        email=active.email or user.email or "",
        phone=active.phone or "",
        linkedin=active.linkedin or "",
        github=active.github or "",
        website=active.website or "",
        location_preference=active.location_preference or "Remote / Worldwide",
        years_experience=active.years_experience or "4+",
        work_authorization=active.work_authorization or "Eligible to work remotely",
        salary_expectation=active.salary_expectation or "",
        earliest_start=active.earliest_start or "2–4 weeks",
        note="",
        master_cv_path="",
        profile_json="{}",
        career_facts_json=json.dumps([]),
        profile_confirmed=False,
    )
    db.add(profile)
    db.flush()
    ensure_profile_billing(db, user, profile)
    profile_data_dir(user.id, profile.id)
    if switch_to:
        user.active_profile_id = profile.id
        db.add(user)
    db.flush()
    return profile


def switch_active_profile(db: Session, user: User, profile_id: int) -> Profile:
    ensure_account(db, user)
    row = db.get(Profile, profile_id)
    if row is None or row.user_id != user.id or row.archived_at is not None:
        raise ValueError("Profile not found")
    user.active_profile_id = row.id
    db.add(user)
    db.flush()
    return row


def rename_profile(db: Session, user: User, profile_id: int, label: str) -> Profile:
    ensure_account(db, user)
    row = db.get(Profile, profile_id)
    if row is None or row.user_id != user.id or row.archived_at is not None:
        raise ValueError("Profile not found")
    row.label = _slug_label(label)
    db.add(row)
    db.flush()
    return row


def archive_profile(db: Session, user: User, profile_id: int) -> Profile:
    """Soft-delete a profile. Blocks last profile and active paid subscriptions."""
    ensure_account(db, user)
    row = db.get(Profile, profile_id)
    if row is None or row.user_id != user.id or row.archived_at is not None:
        raise ValueError("Profile not found")
    alive = _alive_profiles_q(db, user).count()
    if alive <= 1:
        raise ValueError("You need at least one active profile.")
    billing = ensure_profile_billing(db, user, row)
    status = (billing.subscription_status or "").lower()
    if billing.plan in PAID_PLANS and status in _ACTIVE_SUB:
        raise ValueError(
            "Cancel this profile’s active subscription in Billing before archiving it."
        )
    row.archived_at = datetime.utcnow()
    db.add(row)
    if user.active_profile_id == row.id:
        next_row = _alive_profiles_q(db, user).filter(Profile.id != row.id).first()
        if next_row is None:
            raise ValueError("You need at least one active profile.")
        user.active_profile_id = next_row.id
        db.add(user)
    db.flush()
    return row


def get_profile_billing(
    db: Session, user: User, profile: Profile | None = None
) -> ProfileBilling:
    profile = profile or get_active_profile(db, user)
    return ensure_profile_billing(db, user, profile)


def load_user_profile_dict(db: Session, user: User) -> dict[str, Any]:
    row = get_active_profile(db, user)
    try:
        profile = json.loads(row.profile_json or "{}")
    except json.JSONDecodeError:
        profile = {}
    if not isinstance(profile, dict):
        profile = {}
    profile.setdefault("name", row.full_name or user.full_name or "")
    profile.setdefault("skills", (load_user_settings(db, user).get("profile_skills") or []))
    profile.setdefault("summary", "")
    return profile


def apply_profile_from_user(db: Session, user: User) -> dict[str, Any]:
    row = get_active_profile(db, user)
    return {
        "full_name": row.full_name or user.full_name or "",
        "email": row.email or user.email or "",
        "phone": row.phone or "",
        "linkedin": row.linkedin or "",
        "github": row.github or "",
        "website": row.website or "",
        "location_preference": row.location_preference or "",
        "years_experience": row.years_experience or "4+",
        "work_authorization": row.work_authorization or "",
        "salary_expectation": row.salary_expectation or "",
        "earliest_start": row.earliest_start or "",
        "note": row.note or "",
    }


def apply_profile_chips(db: Session, user: User) -> dict[str, Any]:
    """Contact chips for Apply Assist — active profile only."""
    return apply_profile_from_user(db, user)


def is_profile_confirmed(db: Session, user: User) -> bool:
    row = get_active_profile(db, user)
    return bool(row.profile_confirmed)
