from __future__ import annotations

import pytest
from urllib.parse import unquote
from pydantic import ValidationError

from app.auth import UserCreate
from app.db import SessionLocal
from app.models import User, UserRole
from app.roles import is_admin


def test_user_create_forbids_privilege_fields():
    with pytest.raises(ValidationError):
        UserCreate(
            email="evil@example.com",
            password="password123",
            role="super_admin",  # type: ignore[call-arg]
        )


def test_register_creates_basic_role(client, register_user):
    register_user(email="basic@example.com")
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "basic@example.com").one()
        assert user.role == UserRole.BASIC.value
        assert user.is_superuser is False
        assert user.is_verified is True
        assert not is_admin(user)
    finally:
        db.close()


def test_registered_user_can_login(client, register_user, csrf_token):
    register_user(email="login@example.com", password="password123")
    r = client.post(
        "/login",
        data={
            "email": "login@example.com",
            "password": "password123",
            "next": "/",
            "csrf_token": csrf_token,
        },
        follow_redirects=False,
    )
    assert r.status_code in {302, 303}
    assert client.cookies.get("jobmatch_auth")


def test_admin_route_forbidden_for_basic(client, confirmed_user):
    r = client.get("/admin", follow_redirects=False)
    # ForbiddenFlash redirects with flash
    assert r.status_code in {302, 303}
    loc = r.headers.get("location", "")
    assert "Admin access required" in loc or loc.startswith("/")

