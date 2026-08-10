from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

from fastapi import Depends, Request
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin, schemas
from fastapi_users.authentication import (
    AuthenticationBackend,
    CookieTransport,
    JWTStrategy,
)
from fastapi_users.db import SQLAlchemyUserDatabase
from httpx_oauth.clients.github import GitHubOAuth2
from httpx_oauth.clients.google import GoogleOAuth2
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from app.config import database_url, get_settings
from app.db import SessionLocal
from app.models import OAuthAccount, User, UserRole
from app.roles import apply_role, normalize_role

# Persist login ~30 days; cookie is re-issued on authenticated requests (sliding).
SESSION_MAX_AGE = 60 * 60 * 24 * 30


def _async_database_url() -> str:
    url = database_url()
    if url.startswith("sqlite:///"):
        return "sqlite+aiosqlite:///" + url.removeprefix("sqlite:///")
    return url


_ASYNC_URL = _async_database_url()
_async_engine = create_async_engine(_ASYNC_URL, pool_pre_ping=True)
_async_session_maker = async_sessionmaker(_async_engine, expire_on_commit=False)


class UserRead(schemas.BaseUser[uuid.UUID]):
    full_name: str = ""
    role: str = UserRole.BASIC.value


class UserCreate(schemas.BaseUserCreate):
    full_name: str = ""


class UserUpdate(schemas.BaseUserUpdate):
    full_name: str | None = None


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    reset_password_token_secret = get_settings().secret_key or "dev-insecure-secret-change-me-32b+"
    verification_token_secret = get_settings().secret_key or "dev-insecure-secret-change-me-32b+"

    async def create(
        self,
        user_create: schemas.UC,
        safe: bool = False,
        request: Request | None = None,
    ) -> User:
        # Always create as basic; ignore is_superuser / is_verified from clients.
        user_create = UserCreate(
            email=user_create.email,
            password=user_create.password,
            full_name=getattr(user_create, "full_name", "") or "",
            is_active=True,
            is_superuser=False,
            is_verified=True,
        )
        user = await super().create(user_create, safe=True, request=request)
        apply_role(user, UserRole.BASIC.value)
        await self.user_db.update(
            user,
            {
                "role": user.role,
                "is_superuser": False,
                "is_verified": True,
            },
        )
        return user

    async def update(
        self,
        user_update: schemas.UU,
        user: User,
        safe: bool = False,
        request: Request | None = None,
    ) -> User:
        # Never allow privilege escalation via fastapi-users flags; roles change via /admin only.
        payload: dict = {}
        if getattr(user_update, "password", None) is not None:
            payload["password"] = user_update.password
        if getattr(user_update, "email", None) is not None:
            payload["email"] = user_update.email
        if getattr(user_update, "full_name", None) is not None:
            payload["full_name"] = user_update.full_name
        stripped = UserUpdate(**payload)
        updated = await super().update(stripped, user, safe=True, request=request)
        role = normalize_role(getattr(updated, "role", None) or getattr(user, "role", None))
        updated.role = role
        updated.is_superuser = False
        updated.is_verified = True
        await self.user_db.update(
            updated,
            {
                "role": role,
                "is_superuser": False,
                "is_verified": True,
            },
        )
        return updated

    async def on_after_register(self, user: User, request: Request | None = None) -> None:
        from app.accounts import ensure_account

        db = SessionLocal()
        try:
            apply_role(user, UserRole.BASIC.value)
            ensure_account(db, user)
            db.commit()
        finally:
            db.close()


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with _async_session_maker() as session:
        yield session


async def get_user_db(
    session: AsyncSession = Depends(get_async_session),
) -> AsyncGenerator[SQLAlchemyUserDatabase, None]:
    yield SQLAlchemyUserDatabase(session, User, OAuthAccount)


async def get_user_manager(
    user_db: SQLAlchemyUserDatabase = Depends(get_user_db),
) -> AsyncGenerator[UserManager, None]:
    yield UserManager(user_db)


cookie_transport = CookieTransport(
    cookie_name="jobmatch_auth",
    cookie_max_age=SESSION_MAX_AGE,
    cookie_secure=get_settings().oauth_redirect_base.startswith("https"),
    cookie_httponly=True,
    cookie_samesite="lax",
)


def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(
        secret=get_settings().secret_key or "dev-insecure-secret-change-me-32b+",
        lifetime_seconds=SESSION_MAX_AGE,
    )


auth_backend = AuthenticationBackend(
    name="cookie",
    transport=cookie_transport,
    get_strategy=get_jwt_strategy,
)

fastapi_users = FastAPIUsers[User, uuid.UUID](get_user_manager, [auth_backend])

current_active_user = fastapi_users.current_user(active=True)
optional_current_user = fastapi_users.current_user(active=True, optional=True)

settings = get_settings()
google_oauth_client = (
    GoogleOAuth2(settings.google_oauth_client_id, settings.google_oauth_client_secret)
    if settings.google_oauth_client_id and settings.google_oauth_client_secret
    else None
)
github_oauth_client = (
    GitHubOAuth2(settings.github_oauth_client_id, settings.github_oauth_client_secret)
    if settings.github_oauth_client_id and settings.github_oauth_client_secret
    else None
)


def sync_user_from_db(db: Session, user_id: uuid.UUID) -> User | None:
    return db.query(User).filter(User.id == user_id).one_or_none()
