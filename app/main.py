from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.auth import (
    UserCreate,
    UserRead,
    UserUpdate,
    auth_backend,
    fastapi_users,
    github_oauth_client,
    google_oauth_client,
)
from app.config import assert_secure_settings, ensure_dirs, get_settings, project_path
from app.db import init_db
from app.routes import admin, auth_pages, billing, jobs, onboarding, profiles, settings
from app.scheduler import start_catalogue_sync_task
from app.web_helpers import LoginRequired, OnboardingRequired, ForbiddenFlash, safe_http_url


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_dirs()
    assert_secure_settings()
    init_db()
    tasks = start_catalogue_sync_task()
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass


ensure_dirs()
assert_secure_settings()
init_db()

app = FastAPI(title="Vitae", lifespan=lifespan)
templates = Jinja2Templates(directory=str(project_path("app", "templates")))
templates.env.filters["path_quote"] = lambda value: quote(str(value), safe="")
templates.env.filters["safe_http_url"] = safe_http_url

static_dir = project_path("app", "static")
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

settings_cfg = get_settings()
_oauth_cookie_secure = settings_cfg.oauth_redirect_base.startswith("https")

app.include_router(
    fastapi_users.get_auth_router(auth_backend),
    prefix="/auth",
    tags=["auth"],
)
app.include_router(
    fastapi_users.get_register_router(UserRead, UserCreate),
    prefix="/auth",
    tags=["auth"],
)
app.include_router(
    fastapi_users.get_users_router(UserRead, UserUpdate),
    prefix="/users",
    tags=["users"],
)

if google_oauth_client:
    app.include_router(
        fastapi_users.get_oauth_router(
            google_oauth_client,
            auth_backend,
            settings_cfg.secret_key,
            associate_by_email=True,
            is_verified_by_default=True,
            redirect_url=f"{settings_cfg.oauth_redirect_base.rstrip('/')}/auth/google/callback",
            csrf_token_cookie_secure=_oauth_cookie_secure,
        ),
        prefix="/auth/google",
        tags=["auth"],
    )
else:

    @app.get("/auth/google/authorize")
    def google_oauth_unconfigured():
        return RedirectResponse(
            url=(
                "/login?error="
                + quote("Google sign-in is not configured. Add GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET.")
            ),
            status_code=303,
        )


if github_oauth_client:
    app.include_router(
        fastapi_users.get_oauth_router(
            github_oauth_client,
            auth_backend,
            settings_cfg.secret_key,
            associate_by_email=True,
            is_verified_by_default=True,
            redirect_url=f"{settings_cfg.oauth_redirect_base.rstrip('/')}/auth/github/callback",
            csrf_token_cookie_secure=_oauth_cookie_secure,
        ),
        prefix="/auth/github",
        tags=["auth"],
    )
else:

    @app.get("/auth/github/authorize")
    def github_oauth_unconfigured():
        return RedirectResponse(
            url=(
                "/login?error="
                + quote("GitHub sign-in is not configured. Add GITHUB_OAUTH_CLIENT_ID and GITHUB_OAUTH_CLIENT_SECRET.")
            ),
            status_code=303,
        )

app.include_router(auth_pages.router)
app.include_router(onboarding.router)
app.include_router(jobs.router)
app.include_router(settings.router)
app.include_router(profiles.router)
app.include_router(billing.router)
app.include_router(admin.router)


@app.exception_handler(LoginRequired)
async def login_required_handler(request: Request, exc: LoginRequired):
    return RedirectResponse(
        url=f"/login?next={quote(exc.next_path)}",
        status_code=303,
    )


@app.exception_handler(OnboardingRequired)
async def onboarding_required_handler(request: Request, exc: OnboardingRequired):
    return RedirectResponse(url="/onboarding", status_code=303)


@app.exception_handler(ForbiddenFlash)
async def forbidden_flash_handler(request: Request, exc: ForbiddenFlash):
    return RedirectResponse(
        url=f"{exc.path}?flash={quote(exc.message)}",
        status_code=303,
    )


@app.get("/health")
def health():
    return {"ok": True}
