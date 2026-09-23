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
from app.models import (
    Profile,
    ProfileBilling,
    ProfileEducation,
    ProfileExperience,
    ProfileProject,
    ProfileSkill,
    User,
)

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


def _sync_user(db: Session, user: User) -> User:
    """Load the User row on the sync Session (auth may use a different session)."""
    sync = db.get(User, user.id)
    if sync is None:
        raise ValueError("User not found")
    return sync


def _persist_active_profile_id(db: Session, user: User, profile_id: int) -> None:
    """Persist active profile on the account row so auto-login restores it."""
    sync = _sync_user(db, user)
    if sync.active_profile_id != profile_id:
        sync.active_profile_id = profile_id
        db.add(sync)
    # Keep the request-scoped user object consistent for the rest of the call.
    user.active_profile_id = profile_id


def _owned_alive_profile(db: Session, user: User, profile_id: int) -> Profile:
    row = db.get(Profile, profile_id)
    if row is None or row.user_id != user.id or row.archived_at is not None:
        raise ValueError("Profile not found")
    return row


def _alive_profiles_q(db: Session, user: User):
    return (
        db.query(Profile)
        .filter(Profile.user_id == user.id, Profile.archived_at.is_(None))
        .order_by(Profile.id.asc())
    )


def get_active_profile(db: Session, user: User) -> Profile:
    """Return the user's active career profile (creates Default if needed).

    `user.active_profile_id` is stored on the account and survives logout / auto-login.
    """
    ensure_account(db, user)
    sync = _sync_user(db, user)
    if sync.active_profile_id:
        row = db.get(Profile, sync.active_profile_id)
        if row is not None and row.user_id == user.id and row.archived_at is None:
            user.active_profile_id = row.id
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
    _persist_active_profile_id(db, user, row.id)
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
        _persist_active_profile_id(db, user, profile.id)
    db.flush()
    return profile


def switch_active_profile(db: Session, user: User, profile_id: int) -> Profile:
    ensure_account(db, user)
    row = _owned_alive_profile(db, user, profile_id)
    _persist_active_profile_id(db, user, row.id)
    db.flush()
    return row


def rename_profile(db: Session, user: User, profile_id: int, label: str) -> Profile:
    ensure_account(db, user)
    row = _owned_alive_profile(db, user, profile_id)
    row.label = _slug_label(label)
    db.add(row)
    db.flush()
    return row


def archive_profile(db: Session, user: User, profile_id: int) -> Profile:
    """Soft-delete a profile. Blocks last profile and active paid subscriptions."""
    ensure_account(db, user)
    row = _owned_alive_profile(db, user, profile_id)
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
    sync = _sync_user(db, user)
    if sync.active_profile_id == row.id:
        next_row = _alive_profiles_q(db, user).filter(Profile.id != row.id).first()
        if next_row is None:
            raise ValueError("You need at least one active profile.")
        _persist_active_profile_id(db, user, next_row.id)
    db.flush()
    return row


def get_profile_billing(
    db: Session, user: User, profile: Profile | None = None
) -> ProfileBilling:
    profile = profile or get_active_profile(db, user)
    return ensure_profile_billing(db, user, profile)


def load_user_profile_dict(db: Session, user: User) -> dict[str, Any]:
    """Build the profile read model from normalized career tables.

    Legacy profile_json is used only for fields that are not yet normalized.
    This keeps existing callers compatible while the data model is migrated.
    """
    row = get_active_profile(db, user)
    try:
        legacy = json.loads(row.profile_json or "{}")
    except json.JSONDecodeError:
        legacy = {}
    if not isinstance(legacy, dict):
        legacy = {}

    skills = [
        item.name
        for item in db.query(ProfileSkill)
        .filter(ProfileSkill.profile_id == row.id)
        .order_by(ProfileSkill.sort_order.asc(), ProfileSkill.id.asc())
        .all()
        if item.name.strip()
    ]
    experience_rows = (
        db.query(ProfileExperience)
        .filter(ProfileExperience.profile_id == row.id)
        .order_by(ProfileExperience.sort_order.asc(), ProfileExperience.id.asc())
        .all()
    )
    experiences = [
        {
            "id": item.id,
            "position": item.position,
            "company": item.company,
            "location": item.location,
            "start_date": item.start_date,
            "end_date": item.end_date,
            "description": item.description,
        }
        for item in experience_rows
        if item.position.strip() or item.company.strip() or item.description.strip()
    ]
    projects = [
        item.description
        for item in db.query(ProfileProject)
        .filter(ProfileProject.profile_id == row.id)
        .order_by(ProfileProject.sort_order.asc(), ProfileProject.id.asc())
        .all()
        if item.description.strip()
    ]
    education = [
        item.description
        for item in db.query(ProfileEducation)
        .filter(ProfileEducation.profile_id == row.id)
        .order_by(ProfileEducation.sort_order.asc(), ProfileEducation.id.asc())
        .all()
        if item.description.strip()
    ]

    if not skills:
        skills = [
            str(s).strip()
            for s in (legacy.get("skills") or load_user_settings(db, user).get("profile_skills") or [])
            if str(s).strip()
        ]

    profile = dict(legacy)
    profile["name"] = row.full_name or user.full_name or profile.get("name") or ""
    profile["summary"] = str(profile.get("summary") or "").strip()
    profile["skills"] = skills
    profile["experience"] = experiences
    profile["experience_raw"] = [
        " — ".join(
            part
            for part in (
                (
                    f"{item['position']} at {item['company']}"
                    if item["position"] and item["company"]
                    else item["position"] or item["company"]
                ),
                item["description"],
            )
            if part
        )
        for item in experiences
    ]
    profile["projects_raw"] = projects
    profile["education_raw"] = education
    return profile


def apply_profile_from_user(db: Session, user: User) -> dict[str, Any]:
    """Return the normalized profile read model used by application workflows."""
    row = get_active_profile(db, user)
    profile = load_user_profile_dict(db, user)
    try:
        career_facts = json.loads(row.career_facts_json or "[]")
    except json.JSONDecodeError:
        career_facts = []
    if not isinstance(career_facts, list):
        career_facts = []

    return {
        "full_name": row.full_name or user.full_name or profile.get("name") or "",
        "email": row.email or user.email or "",
        "phone": row.phone or "",
        "linkedin": row.linkedin or "",
        "github": row.github or "",
        "website": row.website or "",
        "location_preference": row.location_preference or "",
        "years_experience": row.years_experience or "",
        "work_authorization": row.work_authorization or "",
        "salary_expectation": row.salary_expectation or "",
        "earliest_start": row.earliest_start or "",
        "note": row.note or "",
        "summary": str(profile.get("summary") or "").strip(),
        "skills": [str(s).strip() for s in profile.get("skills") or [] if str(s).strip()],
        "career_facts": [str(f).strip() for f in career_facts if str(f).strip()],
        "experience_highlights": [
            str(line).strip()
            for line in profile.get("experience_raw") or []
            if str(line).strip()
        ][:40],
    }


def apply_profile_chips(db: Session, user: User) -> dict[str, Any]:
    """Contact chips for Apply Assist — active profile only."""
    return apply_profile_from_user(db, user)


def is_profile_confirmed(db: Session, user: User) -> bool:
    row = get_active_profile(db, user)
    return bool(row.profile_confirmed)
