from __future__ import annotations

import asyncio
import os
import secrets
import sys
from contextlib import asynccontextmanager
from urllib.parse import quote

# psycopg's async driver cannot run on Windows' default ProactorEventLoop and
# raises InterfaceError on the first query. Select the selector-based loop
# before any async engine is created. This module is imported by uvicorn before
# the server creates its loop, so setting it here covers `python run.py`,
# `uvicorn app.main:app`, and the test client alike. No-op off Windows.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def selector_loop_factory() -> asyncio.AbstractEventLoop:
    """Event loop factory for servers that bypass the loop policy.

    uvicorn >= 0.36 builds its loop via an explicit ``loop_factory`` that returns
    ``ProactorEventLoop`` on Windows, so the policy set above is not consulted
    and every async psycopg query fails with InterfaceError. Passing this
    factory to uvicorn (``--loop app.main:selector_loop_factory`` or
    ``uvicorn.run(..., loop=...)``) keeps psycopg usable on Windows. Off
    Windows the default selector loop is already correct.
    """
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop()
    return asyncio.new_event_loop()


from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
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
)
from app.config import assert_secure_settings, ensure_dirs, get_settings, project_path, warn_site_settings
from app.csrf import CSRFMiddleware
from app.db import assert_database_migrated
from app.rate_limit import RateLimitExceeded, enforce
from app.routes import auto_apply, admin, applications, auth_pages, billing, jobs, onboarding, profiles, settings, site
from app.scheduler import start_catalogue_sync_task
from app.observability import configure_logging
from app.web_helpers import LoginRequired, OnboardingRequired, ForbiddenFlash, safe_http_url
from app.observability_middleware import ObservabilityMiddleware
from app.worker_metrics import prometheus_snapshot


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
    assert_database_migrated()
    warn_site_settings()
    run_scheduler = os.getenv("RUN_SCHEDULER", "1").strip().lower() not in {"0", "false", "no"}
    tasks = start_catalogue_sync_task() if run_scheduler else []
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


configure_logging()
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
app.add_middleware(ObservabilityMiddleware)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add baseline browser security headers without breaking the existing UI."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if _prod_like:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(AuthApiRateLimitMiddleware)
app.add_middleware(CSRFMiddleware)
templates = Jinja2Templates(directory=str(project_path("app", "templates")))
templates.env.filters["path_quote"] = lambda value: quote(str(value), safe="")
templates.env.filters["safe_http_url"] = safe_http_url

static_dir = project_path("app", "static")
static_dir.mkdir(exist_ok=True)
app.mount("/static", CachedStaticFiles(directory=str(static_dir)), name="static")

app.include_router(
    fastapi_users.get_auth_router(auth_backend, requires_verification=False),
    prefix="/auth",
    tags=["auth"],
)
app.include_router(
    fastapi_users.get_register_router(UserRead, UserCreate),
    prefix="/auth",
    tags=["auth"],
)
app.include_router(
    fastapi_users.get_users_router(UserRead, UserUpdate, requires_verification=False),
    prefix="/users",
    tags=["users"],
)

app.include_router(auth_pages.router)
app.include_router(onboarding.router)
app.include_router(jobs.router)
app.include_router(applications.router)
app.include_router(auto_apply.router)
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


@app.get("/metrics")
def metrics(request: Request):
    """Expose process metrics only when an operator token is configured."""
    token = (get_settings().metrics_token or "").strip()
    if not token:
        return PlainTextResponse("metrics disabled\n", status_code=404)
    supplied = (request.headers.get("Authorization") or "").strip()
    if not secrets.compare_digest(supplied, f"Bearer {token}"):
        return PlainTextResponse("unauthorized\n", status_code=401)
    return PlainTextResponse(prometheus_snapshot(), media_type="text/plain; version=0.0.4")

@app.get("/health")
def health():
    return {"ok": True, "service": "web"}

@app.get("/health/ready")
def readiness():
    assert_database_migrated()
    return {"ok": True, "database": "ready"}


