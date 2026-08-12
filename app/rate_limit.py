"""In-process fixed-window rate limiting (no Redis)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from fastapi import Request

_lock = threading.Lock()
# key -> (window_start_epoch, count)
_buckets: dict[str, tuple[float, int]] = {}

# Hardcoded buckets (requests per 60s). Not env-tunable.
_LIMIT_AUTH = 10
_LIMIT_PASTE = 20
_LIMIT_AI = 15
_LIMIT_DEFAULT = 60


class RateLimitExceeded(Exception):
    def __init__(self, message: str = "Too many requests. Try again shortly.", *, path: str = "/"):
        self.message = message
        self.path = path or "/"
        super().__init__(message)


def reset_rate_limits() -> None:
    """Clear all buckets (tests)."""
    with _lock:
        _buckets.clear()


def check_rate_limit(key: str, *, limit: int, window_seconds: int = 60) -> None:
    """Increment the counter for ``key``; raise RateLimitExceeded when over limit."""
    if limit <= 0:
        return
    now = time.time()
    with _lock:
        start, count = _buckets.get(key, (now, 0))
        if now - start >= window_seconds:
            start, count = now, 0
        count += 1
        _buckets[key] = (start, count)
        if count > limit:
            raise RateLimitExceeded()


def client_ip(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded:
        return forwarded
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def limit_for(scope: str) -> int:
    if scope == "auth":
        return _LIMIT_AUTH
    if scope == "paste":
        return _LIMIT_PASTE
    if scope == "ai":
        return _LIMIT_AI
    return _LIMIT_DEFAULT


def enforce(
    scope: str,
    *,
    request: Request | None = None,
    user_id: Any = None,
    redirect_path: str = "/",
    window_seconds: int = 60,
) -> None:
    """Enforce a limit keyed by user id when provided, otherwise client IP."""
    limit = limit_for(scope)
    if user_id is not None:
        key = f"{scope}:user:{user_id}"
    else:
        ip = client_ip(request) if request is not None else "unknown"
        key = f"{scope}:ip:{ip}"
    try:
        check_rate_limit(key, limit=limit, window_seconds=window_seconds)
    except RateLimitExceeded as exc:
        exc.path = redirect_path
        raise


def rate_limit(
    scope: str,
    *,
    window_seconds: int = 60,
    redirect_path: str = "/",
) -> Callable[..., Any]:
    """IP-based FastAPI dependency (auth forms / unauthenticated paths)."""

    async def _dep(request: Request) -> None:
        enforce(
            scope,
            request=request,
            redirect_path=redirect_path,
            window_seconds=window_seconds,
        )

    return _dep
