from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.models import Profile
from app.profile.loader import contact_parts_from_profile


def store_cv(
    profile: Profile,
    content: bytes,
    filename: str,
) -> tuple[Path, Path | None]:
    """Store a new CV beside the profile and return (new_path, old_path)."""
    profile_dir = Path(profile.master_cv_path).parent if profile.master_cv_path else None
    if profile_dir is None:
        raise ValueError("Profile has no CV storage directory")

    suffix = Path(filename).suffix.lower()
    stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    new_path = profile_dir / f"cv-{stamp}{suffix}"
    new_path.write_bytes(content)

    old_path = Path(profile.master_cv_path) if profile.master_cv_path else None
    return new_path, old_path


def merge_parsed_cv(profile: Profile, parsed: dict[str, Any], cv_path: Path) -> None:
    """Replace CV-derived data while preserving user-entered fields when the new CV is incomplete."""
    try:
        current = json.loads(profile.profile_json or "{}")
    except json.JSONDecodeError:
        current = {}
    if not isinstance(current, dict):
        current = {}

    merged = dict(current)
    for key in (
        "name",
        "contact",
        "summary",
        "skills",
        "skill_lines",
        "experience_raw",
        "projects_raw",
        "education_raw",
        "full_text",
    ):
        value = parsed.get(key)
        if value:
            merged[key] = value

    merged["master_cv"] = str(cv_path)
    profile.profile_json = json.dumps(merged)
    profile.master_cv_path = str(cv_path)
    profile.profile_confirmed = False

    chips = contact_parts_from_profile(parsed)
    if chips["full_name"]:
        profile.full_name = chips["full_name"]
    if chips["email"]:
        profile.email = chips["email"]
    if chips["phone"]:
        profile.phone = chips["phone"]
    if chips["linkedin"]:
        profile.linkedin = chips["linkedin"]
    if chips["github"]:
        profile.github = chips["github"]
    if chips["website"]:
        profile.website = chips["website"]
