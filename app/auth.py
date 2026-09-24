from __future__ import annotations

import uuid
import asyncio
import smtplib
import logging
from email.message import EmailMessage
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
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import database_url, get_settings, site_base_url
from app.csrf import cookie_secure_flag
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
_async_kwargs: dict = {"pool_pre_ping": True}
if not _ASYNC_URL.startswith("sqlite"):
    _async_kwargs.update({"pool_size": 5, "max_overflow": 10, "pool_recycle": 1800})
_async_engine = create_async_engine(_ASYNC_URL, **_async_kwargs)
_async_session_maker = async_sessionmaker(_async_engine, expire_on_commit=False)


class UserRead(schemas.BaseUser[uuid.UUID]):
    full_name: str = ""
    role: str = UserRole.BASIC.value


class UserCreate(BaseModel):
    """Public registration — no is_superuser / is_verified / role (console-proof)."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = ""


class UserUpdate(BaseModel):
    """Self-service profile update — privilege fields are not accepted."""

    model_config = ConfigDict(extra="forbid")

    password: str | None = Field(default=None, min_length=8, max_length=128)
    email: EmailStr | None = None
    full_name: str | None = None


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    reset_password_token_secret = get_settings().secret_key
    verification_token_secret = get_settings().secret_key

    async def create(
        self,
        user_create: schemas.UC,
        safe: bool = False,
        request: Request | None = None,
    ) -> User:
        # Rebuild from allowed fields only — ignore any privilege keys clients send.
        email = getattr(user_create, "email", None)
        password = getattr(user_create, "password", None)
        if not email or not password:
            raise ValueError("Email and password are required")
        safe_create = schemas.BaseUserCreate(
            email=email,
            password=password,
            is_active=True,
            is_superuser=False,
            is_verified=False,
        )
        user = await super().create(safe_create, safe=True, request=request)
        full_name = (getattr(user_create, "full_name", None) or "")[:255]
        apply_role(user, UserRole.BASIC.value)
        await self.user_db.update(
            user,
            {
                "role": UserRole.BASIC.value,
                "is_superuser": False,
                "is_verified": False,
                "full_name": full_name,
            },
        )
        user.full_name = full_name
        return user

    async def update(
        self,
        user_update: schemas.UU,
        user: User,
        safe: bool = False,
        request: Request | None = None,
    ) -> User:
        # Never allow privilege escalation via API / console payloads.
        email = getattr(user_update, "email", None)
        email_changed = email is not None and email != user.email
        payload: dict = {}
        if getattr(user_update, "password", None) is not None:
            payload["password"] = user_update.password
        if email is not None:
            payload["email"] = email
        stripped = schemas.BaseUserUpdate(**payload)
        updated = await super().update(stripped, user, safe=True, request=request)
        role = normalize_role(getattr(updated, "role", None) or getattr(user, "role", None))
        full_name = getattr(user_update, "full_name", None)
        updates: dict = {
            "role": role,
            "is_superuser": False,
            "is_verified": False if email_changed else user.is_verified,
        }
        if full_name is not None:
            updates["full_name"] = str(full_name)[:255]
            updated.full_name = updates["full_name"]
        updated.role = role
        updated.is_superuser = False
        updated.is_verified = updates["is_verified"]
        await self.user_db.update(updated, updates)
        return updated

    async def on_after_request_verify(
        self,
        user: User,
        token: str,
        request: Request | None = None,
    ) -> None:
        settings = get_settings()
        if not settings.smtp_host:
            raise RuntimeError("SMTP is not configured")
        message = EmailMessage()
        message["Subject"] = "Verify your Vitae account"
        message["From"] = settings.smtp_from
        message["To"] = user.email
        verify_url = f"{site_base_url()}/auth/verify?token={token}"
        message.set_content(
            f"Verify your Vitae account by opening this link:\n\n{verify_url}\n\n"
            "If you did not create this account, you can ignore this email."
        )

        def _send() -> None:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
                if settings.smtp_starttls:
                    smtp.starttls()
                if settings.smtp_username:
                    smtp.login(settings.smtp_username, settings.smtp_password)
                smtp.send_message(message)

        await asyncio.to_thread(_send)

    async def on_after_register(self, user: User, request: Request | None = None) -> None:
        from app.accounts import ensure_account

        db = SessionLocal()
        try:
            apply_role(user, UserRole.BASIC.value)
            ensure_account(db, user)
            db.commit()
        finally:
            db.close()

        try:
            await self.request_verify(user, request)
        except Exception:
            logging.getLogger(__name__).exception("Could not send verification email", extra={"user_id": str(user.id)})


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
    cookie_secure=cookie_secure_flag(),
    cookie_httponly=True,
    cookie_samesite="lax",
)


def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(
        secret=get_settings().secret_key,
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
