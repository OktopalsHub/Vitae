"""Helpers for recording privileged administrative actions."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.models import AdminAuditEvent, User


def record_admin_action(
    db: Session,
    actor: User,
    *,
    action: str,
    resource_type: str = "",
    resource_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> AdminAuditEvent:
    """Write a small, append-only audit event without secrets or request bodies."""
    safe_metadata = metadata or {}
    event = AdminAuditEvent(
        actor_user_id=actor.id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        metadata_json=json.dumps(safe_metadata, sort_keys=True, separators=(",", ":")),
    )
    db.add(event)
    return event
