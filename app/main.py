from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response as StarletteResponse
from starlette.types import Scope

from app.auth import (
    UserCreate,
    UserRead,
    UserUpdate,
    auth_backend,
    fastapi_users,
    github_oauth_client,
    google_oauth_client,
)
from app.config import assert_secure_settings, ensure_dirs, get_settings, project_path, warn_site_settings
from app.csrf import CSRFMiddleware, cookie_secure_flag
from app.rate_limit import RateLimitExceeded, enforce
from app.routes import admin, auth_pages, billing, jobs, onboarding, profiles, settings, site
from app.scheduler import start_catalogue_sync_task
from app.web_helpers import LoginRequired, OnboardingRequired, ForbiddenFlash, safe_http_url


class CachedStaticFiles(StaticFiles):
    """Long-cache static assets (pair with ?v= fingerprint in templates)."""

    async def get_response(self, path: str, scope: Scope) -> StarletteResponse:
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


class AuthApiRateLimitMiddleware(BaseHTTPMiddleware):
    """Rate-limit JSON fastapi-users auth endpoints we do not own as route funcs."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path.rstrip("/") or "/"
        if request.method == "POST" and path in {"/auth/login", "/auth/register"}:
            try:
                enforce("auth", request=request, redirect_path="/login")
            except RateLimitExceeded as exc:
                return JSONResponse(
                    {"detail": exc.message},
                    status_code=429,
                )
        return await call_next(request)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_dirs()
    assert_secure_settings()
    warn_site_settings()
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

_settings = get_settings()
_prod_like = (_settings.app_env or "").strip().lower() in {"production", "prod", "cloud"}

app = FastAPI(
    title="Vitae",
    lifespan=lifespan,
    docs_url=None if _prod_like else "/docs",
    redoc_url=None if _prod_like else "/redoc",
    openapi_url=None if _prod_like else "/openapi.json",
)
app.add_middleware(AuthApiRateLimitMiddleware)
app.add_middleware(CSRFMiddleware)
templates = Jinja2Templates(directory=str(project_path("app", "templates")))
templates.env.filters["path_quote"] = lambda value: quote(str(value), safe="")
templates.env.filters["safe_http_url"] = safe_http_url

static_dir = project_path("app", "static")
static_dir.mkdir(exist_ok=True)
app.mount("/static", CachedStaticFiles(directory=str(static_dir)), name="static")

settings_cfg = _settings
_oauth_cookie_secure = cookie_secure_flag()

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
            associate_by_email=False,
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
            associate_by_email=False,
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
app.include_router(site.router)


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


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    accept = (request.headers.get("accept") or "").lower()
    wants_json = "application/json" in accept or request.url.path.startswith("/auth/")
    if wants_json:
        return JSONResponse({"detail": exc.message}, status_code=429)
    from app.web_helpers import flash_redirect

    return flash_redirect(exc.path, exc.message)


@app.get("/health")
def health():
    return {"ok": True}
