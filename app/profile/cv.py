"""CV upload/version lifecycle.

The source document is immutable. Each upload creates a Document and a ResumeVersion.
The latest version becomes active; older versions remain available for audit/history.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models import Document, Profile, ResumeVersion, User


PARSER_NAME = "vitae-cv-parser"
PARSER_VERSION = "1"


def _next_version(db: Session, profile_id: int) -> int:
    latest = (
        db.query(ResumeVersion)
        .filter(ResumeVersion.profile_id == profile_id)
        .order_by(ResumeVersion.version.desc())
        .first()
    )
    return (latest.version + 1) if latest else 1


def register_cv_version(
    db: Session,
    user: User,
    profile: Profile,
    path: Path,
    extracted_profile: dict[str, Any],
    *,
    content_type: str,
) -> ResumeVersion:
    """Persist one immutable CV upload and its parser output.

    This does not commit. Callers can update the Profile and commit the whole
    replacement atomically.
    """
    if profile.user_id != user.id:
        raise PermissionError("Profile does not belong to user")
    if not path.exists() or not path.is_file():
        raise ValueError("CV file does not exist")

    content = path.read_bytes()
    checksum = hashlib.sha256(content).hexdigest()
    version = _next_version(db, profile.id)

    document = Document(
        user_id=user.id,
        profile_id=profile.id,
        kind="resume_source",
        storage_key=str(path),
        original_filename=path.name,
        content_type=content_type,
        size_bytes=len(content),
        checksum=checksum,
    )
    db.add(document)
    db.flush()

    db.query(ResumeVersion).filter(
        ResumeVersion.profile_id == profile.id,
        ResumeVersion.status == "active",
    ).update({"status": "archived"}, synchronize_session=False)

    snapshot = json.dumps(extracted_profile, ensure_ascii=False)
    resume = ResumeVersion(
        profile_id=profile.id,
        source_document_id=document.id,
        version=version,
        parser_name=PARSER_NAME,
        extraction_version=PARSER_VERSION,
        extracted_profile_json=snapshot,
        status="active",
    )
    db.add(resume)
    db.flush()

    profile.master_cv_path = str(path)
    db.add(profile)
    return resume


def list_resume_versions(
    db: Session, user: User, profile_id: int
) -> list[ResumeVersion]:
    """Return resume history only when the profile belongs to the user."""
    profile = db.get(Profile, profile_id)
    if profile is None or profile.user_id != user.id:
        raise PermissionError("Profile does not belong to user")
    return (
        db.query(ResumeVersion)
        .filter(ResumeVersion.profile_id == profile_id)
        .order_by(ResumeVersion.version.desc())
        .all()
    )


def get_active_resume(
    db: Session, user: User, profile_id: int
) -> ResumeVersion | None:
    profile = db.get(Profile, profile_id)
    if profile is None or profile.user_id != user.id:
        raise PermissionError("Profile does not belong to user")
    return (
        db.query(ResumeVersion)
        .filter(
            ResumeVersion.profile_id == profile_id,
            ResumeVersion.status == "active",
        )
        .order_by(ResumeVersion.version.desc())
        .first()
    )
