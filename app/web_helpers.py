from __future__ import annotations

import time
import uuid
from pathlib import Path
from urllib.parse import quote, urlparse

from fastapi import Depends, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.generator import ensure_apply_copy
from app.auth import SESSION_MAX_AGE, get_jwt_strategy, optional_current_user
from app.csrf import cookie_secure_flag, csrf_token_for_template
from app.config import api_key_status, get_settings, site_base_url, uses_sqlite
from app.db import get_db
from app.models import User
from app.accounts import (
    apply_profile_from_user,
    can_use_ai,
    ensure_account,
    is_profile_confirmed,
    llm_creds_for_user,
    load_apply_draft_db,
    save_apply_draft_db,
)
from app.accounts.profile import (
    get_active_profile,
    get_profile_billing,
    list_user_profiles,
)

# Re-issue JWT at most this often (seconds). Login still writes a fresh cookie.
_SLIDE_INTERVAL_SECONDS = 6 * 60 * 60
_SLIDE_MARKER_COOKIE = "vitae_auth_slide"


class LoginRequired(Exception):
    def __init__(self, next_path: str = "/jobs"):
        self.next_path = next_path or "/jobs"


class OnboardingRequired(Exception):
    pass


class ForbiddenFlash(Exception):
    def __init__(self, message: str = "Access denied", path: str = "/jobs"):
        self.message = message
        self.path = path or "/jobs"


def safe_next_path(raw: str | None, default: str = "/jobs") -> str:
    """Only allow same-site relative redirects (block open redirects)."""
    value = (raw or default or "/jobs").strip() or "/jobs"
    if not value.startswith("/") or value.startswith("//"):
        return default
    if "\\" in value or "\n" in value or "\r" in value:
        return default
    return value


def safe_http_url(url: str | None) -> str:
    """Return url only if scheme is http(s); else empty (blocks javascript: XSS)."""
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
        return raw
    return ""


def flash_redirect(path: str, message: str) -> RedirectResponse:
    sep = "&" if "?" in path else "?"
    return RedirectResponse(url=f"{path}{sep}flash={quote(message)}", status_code=303)


def redirect_with_auth_cookie(url: str, auth_response) -> RedirectResponse:
    redirect = RedirectResponse(url=url, status_code=303)
    for key, value in auth_response.raw_headers:
        if key.lower() == b"set-cookie":
            redirect.raw_headers.append((key, value))
    return redirect


async def _slide_session_cookie(request: Request, response: Response, user: User) -> None:
    """Re-issue JWT cookie periodically so active users stay signed in."""
    now = int(time.time())
    raw = (request.cookies.get(_SLIDE_MARKER_COOKIE) or "").strip()
    try:
        last = int(raw) if raw else 0
    except ValueError:
        last = 0
    if last and (now - last) < _SLIDE_INTERVAL_SECONDS:
        return
    token = await get_jwt_strategy().write_token(user)
    secure = cookie_secure_flag()
    response.set_cookie(
        key="jobmatch_auth",
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )
    response.set_cookie(
        key=_SLIDE_MARKER_COOKIE,
        value=str(now),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


async def require_user(
    request: Request,
    response: Response,
    user: User | None = Depends(optional_current_user),
) -> User:
    if user is None:
        path = request.url.path
        if request.url.query:
            path = f"{path}?{request.url.query}"
        raise LoginRequired(path)
    await _slide_session_cookie(request, response, user)
    return user


async def require_admin_user(user: User = Depends(require_user)) -> User:
    from app.roles import is_admin

    if not is_admin(user):
        raise ForbiddenFlash("Admin access required.", "/")
    return user


async def require_super_admin_user(user: User = Depends(require_user)) -> User:
    from app.roles import is_super_admin

    if not is_super_admin(user):
        raise ForbiddenFlash("Super admin access required.", "/")
    return user


async def require_profile_ready(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> User:
    ensure_account(db, user)
    if not is_profile_confirmed(db, user):
        raise OnboardingRequired()
    return user


def canonical_url(request: Request) -> str:
    """Absolute canonical URL for the current page (public SEO)."""
    base = site_base_url()
    path = request.url.path or "/"
    if request.url.query:
        # Drop tracking/query params from canonical — keep path only for app pages.
        pass
    return f"{base}{path}"


def template_ctx(
    request: Request,
    user: User | None,
    db: Session | None = None,
    **extra,
) -> dict:
    # Allow routes to pass precomputed nav/billing flags to avoid duplicate queries.
    pre_ai_ok = extra.pop("ai_ok", None)
    pre_ai_reason = extra.pop("ai_reason", None)
    pre_full_access = extra.pop("full_access", None)
    pre_profile_ready = extra.pop("profile_ready", None)
    pre_active = extra.pop("active_profile", None)
    pre_nav = extra.pop("nav_profiles", None)
    pre_billing = extra.pop("billing", None)

    ai_ok, ai_reason = (False, "")
    billing = pre_billing
    full_access = False
    profile_ready = False
    active_profile = pre_active
    nav_profiles: list[dict] = list(pre_nav) if pre_nav is not None else []

    if user and db is not None:
        ensure_account(db, user)
        if pre_ai_ok is None:
            ai_ok, ai_reason = can_use_ai(db, user)
        else:
            ai_ok, ai_reason = bool(pre_ai_ok), (pre_ai_reason or "")
        if active_profile is None:
            active_profile = get_active_profile(db, user)
        if billing is None:
            billing = get_profile_billing(db, user, active_profile)
        from app.accounts import has_full_job_access

        if pre_full_access is None:
            full_access = has_full_job_access(db, user)
        else:
            full_access = bool(pre_full_access)
        if pre_profile_ready is None:
            profile_ready = is_profile_confirmed(db, user)
        else:
            profile_ready = bool(pre_profile_ready)
        if pre_nav is None:
            nav_profiles = [
                {
                    "id": p.id,
                    "label": p.label,
                    "active": p.id == active_profile.id,
                    "confirmed": bool(p.profile_confirmed),
                }
                for p in list_user_profiles(db, user)
            ]
    elif pre_ai_ok is not None:
        ai_ok, ai_reason = bool(pre_ai_ok), (pre_ai_reason or "")
        full_access = bool(pre_full_access) if pre_full_access is not None else False
        profile_ready = bool(pre_profile_ready) if pre_profile_ready is not None else False

    from app.auth import github_oauth_client, google_oauth_client
    from app.roles import is_admin, is_super_admin, user_role

    settings = get_settings()

    ctx = {
        "request": request,
        "user": user,
        "keys": api_key_status(),
        "flash": request.query_params.get("flash", "") if request is not None else "",
        "uses_sqlite": uses_sqlite(),
        "ai_ok": ai_ok,
        "ai_reason": ai_reason,
        "billing": billing,
        "full_access": full_access,
        "profile_ready": profile_ready,
        "active_profile": active_profile,
        "nav_profiles": nav_profiles,
        "csrf_token": csrf_token_for_template(request) if request is not None else "",
        "google_oauth": bool(google_oauth_client),
        "github_oauth": bool(github_oauth_client),
        "user_role": user_role(user) if user else "basic",
        "is_admin": is_admin(user),
        "is_super_admin": is_super_admin(user),
        "canonical_url": canonical_url(request) if request is not None else site_base_url(),
        "site_url": site_base_url(),
        "google_site_verification": (settings.google_site_verification or "").strip(),
    }
    ctx.update(extra)
    return ctx


async def ensure_user_apply_copy(
    db: Session,
    user: User,
    card,
    *,
    force_cover: bool = False,
    force_answers: bool = False,
    use_ai: bool = True,
):
    existing = load_apply_draft_db(db, user, card.id)
    creds = llm_creds_for_user(db, user) if use_ai else None
    if use_ai:
        ok, _ = can_use_ai(db, user)
        if not ok:
            creds = None
    copy = await ensure_apply_copy(
        card,
        apply_profile_from_user(db, user),
        force_cover=force_cover,
        force_answers=force_answers,
        creds=creds,
        existing=existing,
    )
    save_apply_draft_db(db, user, card.id, copy)
    return copy


def assert_download_under_user(user_id: uuid.UUID, output_dir: str, filename: str) -> Path:
    """Resolve a download path that must live under the user's data directory."""
    from app.accounts import user_data_dir

    root = user_data_dir(user_id).resolve()
    out = Path(output_dir).resolve()
    try:
        out.relative_to(root)
    except ValueError as exc:
        raise PermissionError("Invalid output directory") from exc
    safe = Path(filename).name
    path = (out / safe).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise PermissionError("Invalid file path") from exc
    return path


def validate_upload_filename(filename: str | None) -> str:
    name = Path(filename or "").name
    lower = name.lower()
    if not (lower.endswith(".pdf") or lower.endswith(".docx")):
        raise ValueError("Upload a PDF or DOCX file")
    return name


def validate_upload_content(filename: str, content: bytes) -> None:
    """Reject files whose magic bytes do not match the declared extension."""
    lower = Path(filename).name.lower()
    if lower.endswith(".pdf"):
        if not content.lstrip().startswith(b"%PDF"):
            raise ValueError("File content is not a valid PDF")
        return
    if lower.endswith(".docx"):
        # DOCX is a ZIP package (PK\x03\x04 or empty-archive PK\x05\x06).
        if len(content) < 4 or content[:2] != b"PK":
            raise ValueError("File content is not a valid DOCX")
        return
    raise ValueError("Upload a PDF or DOCX file")


def read_upload_limited(content: bytes) -> bytes:
    max_bytes = get_settings().max_upload_bytes
    if len(content) > max_bytes:
        raise ValueError(f"File too large (max {max_bytes // (1024 * 1024)}MB)")
    if not content:
        raise ValueError("Empty file")
    return content
