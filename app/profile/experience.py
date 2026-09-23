"""First-class profile work experience operations."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Profile, ProfileExperience, User


def _owned_profile(db: Session, user: User, profile_id: int) -> Profile:
    profile = db.get(Profile, profile_id)
    if profile is None or profile.user_id != user.id or profile.archived_at is not None:
        raise ValueError("Profile not found")
    return profile


def list_experience(db: Session, user: User, profile_id: int) -> list[ProfileExperience]:
    _owned_profile(db, user, profile_id)
    return (
        db.query(ProfileExperience)
        .filter(ProfileExperience.profile_id == profile_id)
        .order_by(ProfileExperience.sort_order.asc(), ProfileExperience.id.asc())
        .all()
    )


def create_experience(
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
    last = (
        db.query(ProfileExperience)
        .filter(ProfileExperience.profile_id == profile_id)
        .order_by(ProfileExperience.sort_order.desc(), ProfileExperience.id.desc())
        .first()
    )
    item = ProfileExperience(
        profile_id=profile_id,
        position=position.strip()[:255],
        company=company.strip()[:255],
        location=location.strip()[:255],
        start_date=start_date.strip()[:64],
        end_date=end_date.strip()[:64],
        description=description.strip()[:10000],
        sort_order=(last.sort_order + 1) if last else 0,
    )
    if not item.position or not item.company:
        raise ValueError("Position and company are required.")
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
        raise ValueError("Experience not found")
    _owned_profile(db, user, item.profile_id)
    position = position.strip()[:255]
    company = company.strip()[:255]
    if not position or not company:
        raise ValueError("Position and company are required.")
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
        raise ValueError("Experience not found")
    _owned_profile(db, user, item.profile_id)
    db.delete(item)
    db.flush()


def move_experience(
    db: Session, user: User, experience_id: int, direction: str
) -> None:
    item = db.get(ProfileExperience, experience_id)
    if item is None:
        raise ValueError("Experience not found")
    _owned_profile(db, user, item.profile_id)

    items = list_experience(db, user, item.profile_id)
    index = next(i for i, row in enumerate(items) if row.id == item.id)
    target_index = index - 1 if direction == "up" else index + 1
    if target_index < 0 or target_index >= len(items):
        return

    other = items[target_index]
    item.sort_order, other.sort_order = other.sort_order, item.sort_order
    db.add_all([item, other])
    db.flush()
