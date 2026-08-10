from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.accounts.profile import get_active_profile
from app.models import ApplyDraft, User


def load_apply_draft_db(db: Session, user: User, listing_id: int) -> dict[str, Any]:
    active = get_active_profile(db, user)
    row = (
        db.query(ApplyDraft)
        .filter(
            ApplyDraft.profile_id == active.id,
            ApplyDraft.listing_id == listing_id,
        )
        .one_or_none()
    )
    if not row:
        return {}
    try:
        answers = json.loads(row.answers_json or "[]")
    except json.JSONDecodeError:
        answers = []
    return {
        "cover_blurb": row.cover_blurb or "",
        "answers": answers if isinstance(answers, list) else [],
    }


def save_apply_draft_db(
    db: Session, user: User, listing_id: int, data: dict[str, Any]
) -> None:
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
