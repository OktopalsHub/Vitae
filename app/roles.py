"""Pure role helpers — role column is the source of truth (basic / admin / super_admin)."""

from __future__ import annotations

from app.models import User, UserRole

_ROLE_RANK = {
    UserRole.BASIC.value: 0,
    UserRole.ADMIN.value: 1,
    UserRole.SUPER_ADMIN.value: 2,
}

ALLOWED_ROLES = frozenset(_ROLE_RANK)

# Legacy values still accepted when reading old rows.
_LEGACY_ALIASES = {
    "user": UserRole.BASIC.value,
}


def normalize_role(raw: str | None) -> str:
    role = (raw or "").strip().lower()
    role = _LEGACY_ALIASES.get(role, role)
    if role in ALLOWED_ROLES:
        return role
    return UserRole.BASIC.value


def user_role(user: User) -> str:
    return normalize_role(getattr(user, "role", None))


def is_admin(user: User | None) -> bool:
    if user is None:
        return False
    return _ROLE_RANK[user_role(user)] >= _ROLE_RANK[UserRole.ADMIN.value]


def is_super_admin(user: User | None) -> bool:
    if user is None:
        return False
    return user_role(user) == UserRole.SUPER_ADMIN.value


def apply_role(user: User, role: str) -> None:
    """Set app role. fastapi-users flags stay inactive — authz uses role only."""
    normalized = normalize_role(role)
    if normalized not in ALLOWED_ROLES:
        raise ValueError("Invalid role")
    user.role = normalized
    # Columns exist for fastapi-users schema compatibility; never used for authz.
    user.is_superuser = False


def can_assign_role(actor: User, target: User, new_role: str) -> tuple[bool, str]:
    if not is_super_admin(actor):
        return False, "Only super admins can change roles."
    normalized = normalize_role(new_role)
    if normalized not in ALLOWED_ROLES:
        return False, "Invalid role."
    if actor.id == target.id and user_role(actor) == UserRole.SUPER_ADMIN.value:
        if normalized != UserRole.SUPER_ADMIN.value:
            return False, "You cannot demote your own super admin account."
    return True, ""
