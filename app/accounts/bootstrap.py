"""Create missing account rows and disk folders for a user."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.accounts.paths import profile_data_dir, user_data_dir
from app.config import load_yaml_config
from app.models import (
    BillingPlan,
    BillingRegion,
    Profile,
    ProfileBilling,
    User,
    UserSettings,
)


def ensure_profile_billing(db: Session, user: User, profile: Profile) -> ProfileBilling:
    row = db.get(ProfileBilling, profile.id)
    if row is None:
        row = ProfileBilling(
            profile_id=profile.id,
            user_id=user.id,
            plan=BillingPlan.NONE.value,
            billing_region=BillingRegion.NG.value,
        )
        db.add(row)
        db.flush()
    elif row.user_id != user.id:
        row.user_id = user.id
        db.add(row)
        db.flush()
    return row


def ensure_account(db: Session, user: User) -> None:
    """Create Default profile / settings / billing and on-disk folders if missing.

    Does not reset active_profile_id when the user already has a valid one.
    """
    profile = (
        db.query(Profile)
        .filter(Profile.user_id == user.id, Profile.archived_at.is_(None))
        .order_by(Profile.id.asc())
        .first()
    )
    if profile is None:
        profile = (
            db.query(Profile)
            .filter(Profile.user_id == user.id)
            .order_by(Profile.id.asc())
            .first()
        )
        if profile is not None and profile.archived_at is not None:
            profile.archived_at = None
            db.add(profile)
            db.flush()
    if profile is None:
        profile = Profile(
            user_id=user.id,
            label="Default",
            full_name=user.full_name or "",
            email=user.email or "",
            location_preference="Remote / Worldwide",
            work_authorization="Eligible to work remotely",
            salary_expectation="Open to discussion based on role and location",
            earliest_start="2–4 weeks",
            career_facts_json=json.dumps([]),
            profile_json="{}",
        )
        db.add(profile)
        db.flush()
        user.active_profile_id = profile.id
        db.add(user)
    elif user.active_profile_id is None:
        user.active_profile_id = profile.id
        db.add(user)
    else:
        active = db.get(Profile, user.active_profile_id)
        if active is None or active.user_id != user.id or active.archived_at is not None:
            user.active_profile_id = profile.id
            db.add(user)

    if not db.get(UserSettings, user.id):
        defaults = load_yaml_config()
        slim = {
            "search": defaults.get("search") or {},
            "profile_skills": defaults.get("profile_skills") or [],
            "title_keywords": defaults.get("title_keywords") or [],
            "penalty_keywords": defaults.get("penalty_keywords") or [],
            "exclude_title_patterns": defaults.get("exclude_title_patterns") or [],
            "greenhouse_boards": defaults.get("greenhouse_boards") or [],
            "lever_boards": defaults.get("lever_boards") or [],
            "sources": defaults.get("sources") or {},
            "wellfound_urls": defaults.get("wellfound_urls") or [],
            "djinni_urls": defaults.get("djinni_urls") or [],
            "exclude_bullet_patterns": defaults.get("exclude_bullet_patterns") or [],
        }
        db.add(UserSettings(user_id=user.id, settings_json=json.dumps(slim)))

    # Billing row for every profile of this user
    for p in db.query(Profile).filter(Profile.user_id == user.id).all():
        ensure_profile_billing(db, user, p)
        profile_data_dir(user.id, p.id)

    user_data_dir(user.id)
    db.flush()
