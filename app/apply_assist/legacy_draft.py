"""Legacy file-based draft I/O.

DEPRECATED — drafts are now stored in the database (ApplyDraft model).
This module exists only for one-time migration of old per-user JSON drafts.
Delete after DB migration is confirmed complete across all deployments.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.config import project_path

APPLY_PROFILE_PATH = project_path("data", "profile", "apply_profile.json")
DRAFTS_DIR = project_path("data", "apply_drafts")

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_USERS_ROOT = project_path("data", "users")


def _safe_user_dir(user_id: str) -> Path:
    """Return the per-user data directory after asserting no path traversal."""
    uid = str(user_id or "").strip()
    if not _UUID_RE.match(uid):
        raise ValueError(f"Invalid user_id format: {uid!r}")
    resolved = (_USERS_ROOT / uid).resolve()
    if not str(resolved).startswith(str(_USERS_ROOT.resolve())):
        raise ValueError(f"Path traversal detected for user_id {uid!r}")
    return resolved


def _draft_path(job_id: int, user_id: str | None = None) -> Path:
    if user_id:
        path = _safe_user_dir(user_id) / "drafts"
        path.mkdir(parents=True, exist_ok=True)
        return path / f"{int(job_id)}.json"
    return DRAFTS_DIR / f"{int(job_id)}.json"


def load_job_draft(job_id: int, user_id: str | None = None) -> dict[str, Any]:
    path = _draft_path(job_id, user_id=user_id)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def save_job_draft(
    job_id: int, data: dict[str, Any], user_id: str | None = None
) -> dict[str, Any]:
    if not user_id:
        DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    current = load_job_draft(job_id, user_id=user_id)
    current.update(data)
    _draft_path(job_id, user_id=user_id).write_text(
        json.dumps(current, indent=2), encoding="utf-8"
    )
    return current
