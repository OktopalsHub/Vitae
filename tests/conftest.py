"""Pytest fixtures — configure isolated DB/env before importing the app."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="vitae-pytest-"))
_DB = _TMP / "test.db"

os.environ["DATABASE_URL"] = f"sqlite:///{_DB.as_posix()}"
os.environ["APP_ENV"] = "test"
os.environ["SECRET_KEY"] = "test-secret-key-for-pytest-not-default!!"
os.environ["FERNET_SECRET_KEY"] = "test-fernet-key-for-pytest-not-default!!"
os.environ["BACHS_WEBHOOK_SECRET"] = "test-webhook-secret"
os.environ["CATALOGUE_SYNC_DISABLED"] = "1"
os.environ["USER_RANK_REFRESH_DISABLED"] = "1"
# Tests: force intl (no edge) unless a test opts into geo.
os.environ["TRUST_EDGE_GEO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.csrf import CSRF_COOKIE  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User  # noqa: E402
from app.accounts import ensure_account, get_active_profile  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _init_test_db():
    get_settings.cache_clear()
    init_db()
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _csrf(client: TestClient) -> str:
    client.get("/login")
    token = client.cookies.get(CSRF_COOKIE) or ""
    assert len(token) >= 16, "CSRF cookie missing"
    return token


@pytest.fixture
def csrf_token(client: TestClient) -> str:
    return _csrf(client)


@pytest.fixture
def register_user(client: TestClient):
    """Register via HTML form and return credentials."""

    def _register(
        email: str = "user@example.com",
        password: str = "password123",
    ) -> dict[str, str]:
        token = _csrf(client)
        r = client.post(
            "/register",
            data={"email": email, "password": password, "csrf_token": token},
            follow_redirects=False,
        )
        assert r.status_code in {302, 303}, r.text[:500]
        return {"email": email, "password": password, "csrf_token": token}

    return _register


@pytest.fixture
def confirmed_user(client: TestClient, register_user, db_session):
    """Registered user with confirmed profile (skips CV upload)."""
    from app.rate_limit import reset_rate_limits

    reset_rate_limits()
    email = f"confirmed-{os.urandom(4).hex()}@example.com"
    creds = register_user(email=email)
    db = db_session
    user = db.query(User).filter(User.email == email).one()
    ensure_account(db, user)
    profile = get_active_profile(db, user)
    profile.profile_confirmed = True
    profile.full_name = "Confirmed User"
    profile.profile_json = (
        '{"name":"Confirmed User","summary":"Backend engineer","skills":["Python","FastAPI"],'
        '"experience_raw":["Acme Corp — built APIs"],"projects_raw":[],"education_raw":[]}'
    )
    profile.career_facts_json = '["Shipped APIs used by 10k users"]'
    db.add(profile)
    db.commit()
    db.refresh(user)
    return {"user": user, **creds}
