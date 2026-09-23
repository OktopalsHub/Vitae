"""Structured career-experience operations."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.models import Profile, ProfileExperience, User


def _owned_profile(db: Session, user: User, profile_id: int) -> Profile:
    profile = db.get(Profile, profile_id)
    if profile is None or profile.user_id != user.id or profile.archived_at is not None:
        raise PermissionError("Profile not found")
    return profile


def list_experience(db: Session, user: User, profile_id: int) -> list[ProfileExperience]:
    _owned_profile(db, user, profile_id)
    return (
        db.query(ProfileExperience)
        .filter(ProfileExperience.profile_id == profile_id)
        .order_by(ProfileExperience.sort_order.asc(), ProfileExperience.id.asc())
        .all()
    )


def add_experience(
    db: Session,
    user: User,
    profile_id: int,
    *,
    position: str,
    company: str,
    location: str = "",
    start_date: str = "",
    end_date: str = "",
    description: str = "",
) -> ProfileExperience:
    _owned_profile(db, user, profile_id)
    count = db.query(ProfileExperience).filter(ProfileExperience.profile_id == profile_id).count()
    item = ProfileExperience(
        profile_id=profile_id,
        position=position.strip()[:255],
        company=company.strip()[:255],
        location=location.strip()[:255],
        start_date=start_date.strip()[:64],
        end_date=end_date.strip()[:64],
        description=description.strip()[:10000],
        sort_order=count,
    )
    if not item.position or not item.company:
        raise ValueError("Job title and company are required.")
    db.add(item)
    db.flush()
    return item


def update_experience(
    db: Session,
    user: User,
    experience_id: int,
    *,
    position: str,
    company: str,
    location: str = "",
    start_date: str = "",
    end_date: str = "",
    description: str = "",
) -> ProfileExperience:
    item = db.get(ProfileExperience, experience_id)
    if item is None:
        raise ValueError("Experience not found.")
    _owned_profile(db, user, item.profile_id)
    position = position.strip()[:255]
    company = company.strip()[:255]
    if not position or not company:
        raise ValueError("Job title and company are required.")
    item.position = position
    item.company = company
    item.location = location.strip()[:255]
    item.start_date = start_date.strip()[:64]
    item.end_date = end_date.strip()[:64]
    item.description = description.strip()[:10000]
    db.add(item)
    db.flush()
    return item


def delete_experience(db: Session, user: User, experience_id: int) -> None:
    item = db.get(ProfileExperience, experience_id)
    if item is None:
        raise ValueError("Experience not found.")
    _owned_profile(db, user, item.profile_id)
    db.delete(item)
    db.flush()
    remaining = (
        db.query(ProfileExperience)
        .filter(ProfileExperience.profile_id == item.profile_id)
        .order_by(ProfileExperience.sort_order.asc(), ProfileExperience.id.asc())
        .all()
    )
    for index, row in enumerate(remaining):
        row.sort_order = index
        db.add(row)
