"""CSRF protection for cookie-authenticated form POSTs (double-submit cookie)."""

from __future__ import annotations

import hmac
import logging
import secrets
from collections.abc import Callable
from urllib.parse import parse_qs

from starlette.datastructures import UploadFile
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

logger = logging.getLogger(__name__)

CSRF_COOKIE = "vitae_csrf"
CSRF_FORM_FIELD = "csrf_token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
EXEMPT_PREFIXES = (
    "/webhooks/",
    "/health",
    "/static/",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/auth/google/",
    "/auth/github/",
)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def cookie_secure_flag() -> bool:
    from app.config import get_settings

    s = get_settings()
    env = (s.app_env or "").strip().lower()
    if env in {"production", "prod", "cloud"}:
        return True
    return (s.oauth_redirect_base or "").startswith("https")


def ensure_csrf_cookie(request: Request, response: Response) -> str:
    token = (getattr(request.state, "csrf_token", None) or "").strip()
    if len(token) < 16:
        token = (request.cookies.get(CSRF_COOKIE) or "").strip()
    if len(token) < 16:
        token = new_csrf_token()
    response.set_cookie(
        key=CSRF_COOKIE,
        value=token,
        max_age=60 * 60 * 24 * 30,
        httponly=False,
        samesite="lax",
        secure=cookie_secure_flag(),
        path="/",
    )
    request.state.csrf_token = token
    return token


def csrf_token_for_template(request: Request) -> str:
    existing = (getattr(request.state, "csrf_token", None) or "").strip()
    if len(existing) >= 16:
        return existing
    cookie = (request.cookies.get(CSRF_COOKIE) or "").strip()
    if len(cookie) >= 16:
        request.state.csrf_token = cookie
        return cookie
    token = new_csrf_token()
    request.state.csrf_token = token
    return token


def _exempt(path: str) -> bool:
    return any(path == p.rstrip("/") or path.startswith(p) for p in EXEMPT_PREFIXES)


def _tokens_match(a: str, b: str) -> bool:
    if not a or not b or len(a) < 16 or len(b) < 16:
        return False
    return hmac.compare_digest(a, b)


class CSRFMiddleware(BaseHTTPMiddleware):
    """Require csrf_token form field (or X-CSRF-Token) matching vitae_csrf cookie on POSTs."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        if request.method in SAFE_METHODS or _exempt(path):
            response = await call_next(request)
            if request.method in {"GET", "HEAD"}:
                ensure_csrf_cookie(request, response)
            return response

        cookie_token = (request.cookies.get(CSRF_COOKIE) or "").strip()
        header_token = (request.headers.get("x-csrf-token") or "").strip()
        form_token = ""

        content_type = (request.headers.get("content-type") or "").lower()
        if (
            "application/x-www-form-urlencoded" in content_type
            or "multipart/form-data" in content_type
        ):
            body = await request.body()

            async def receive() -> dict:
                return {"type": "http.request", "body": body, "more_body": False}

            request = Request(request.scope, receive)
            try:
                if "application/x-www-form-urlencoded" in content_type:
                    parsed = parse_qs(body.decode("utf-8", errors="ignore"), keep_blank_values=True)
                    vals = parsed.get(CSRF_FORM_FIELD) or []
                    form_token = (vals[0] if vals else "") or ""
                else:
                    form = await request.form()
                    raw = form.get(CSRF_FORM_FIELD) or ""
                    form_token = "" if isinstance(raw, UploadFile) else str(raw)

                async def receive2() -> dict:
                    return {"type": "http.request", "body": body, "more_body": False}

                request = Request(request.scope, receive2)
            except Exception as exc:  # noqa: BLE001
                logger.debug("CSRF form token parse failed: %s", exc)
                form_token = ""

        submitted = header_token or form_token
        if not _tokens_match(cookie_token, submitted):
            # Allow first-time POSTs where cookie was set from form token in same flow
            # (cookie missing but form has token we issued) — still require cookie for CSRF.
            return PlainTextResponse("CSRF validation failed", status_code=403)

        response = await call_next(request)
        ensure_csrf_cookie(request, response)
        return response
