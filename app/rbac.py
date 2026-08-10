"""FastAPI dependencies for role checks (API JSON 403). Prefer web_helpers for HTML."""

from __future__ import annotations

from fastapi import Depends, HTTPException, status

from app.models import User
from app.roles import is_admin, is_super_admin
from app.web_helpers import require_user


async def require_admin(user: User = Depends(require_user)) -> User:
    if not is_admin(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


async def require_super_admin(user: User = Depends(require_user)) -> User:
    if not is_super_admin(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin access required")
    return user
