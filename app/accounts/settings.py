from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.accounts.bootstrap import ensure_account
from app.config import load_yaml_config
from app.models import User, UserSettings


def load_user_settings(db: Session, user: User) -> dict[str, Any]:
    ensure_account(db, user)
    row = db.get(UserSettings, user.id)
    if not row or not row.settings_json:
        return load_yaml_config()
    try:
        data = json.loads(row.settings_json)
        return data if isinstance(data, dict) else load_yaml_config()
    except json.JSONDecodeError:
        return load_yaml_config()


def save_user_settings(db: Session, user: User, data: dict[str, Any]) -> None:
    ensure_account(db, user)
    row = db.get(UserSettings, user.id)
    assert row
    row.settings_json = json.dumps(data)
    db.add(row)


def get_preview_listing_id(db: Session, user: User) -> int | None:
    data = load_user_settings(db, user)
    raw = data.get("preview_unlocked_listing_id")
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def set_preview_listing_id(db: Session, user: User, listing_id: int) -> None:
    data = load_user_settings(db, user)
    data["preview_unlocked_listing_id"] = int(listing_id)
    save_user_settings(db, user, data)
