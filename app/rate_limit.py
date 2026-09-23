"""In-process fixed-window rate limiting (no Redis)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from fastapi import Request\n\nfrom app.config import get_settings

_lock = threading.Lock()
# key -> (window_start_epoch, count)
_buckets: dict[str, tuple[float, int]] = {}

def _limits() -> dict[str, int]:
    s = get_settings()
    return {
        "auth": max(1, int(s.rate_limit_auth)),
        "paste": max(1, int(s.rate_limit_paste)),
        "ai": max(1, int(s.rate_limit_ai)),
        "default": max(1, int(s.rate_limit_default)),
    }


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


def _trusted_proxy(peer: str) -> bool:
    if not peer:
        return False
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        return False
    for raw in (get_settings().trusted_proxy_ips or "").split(","):
        value = raw.strip()
        if not value:
            continue
        try:
            if address in ipaddress.ip_network(value, strict=False):
                return True
        except ValueError:
            continue
    return False


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client and request.client.host else ""
    if _trusted_proxy(peer):
        forwarded = (request.headers.get("x-forwarded-for") or "").split(",")
        for value in reversed(forwarded):
            candidate = value.strip()
            try:
                ipaddress.ip_address(candidate)
            except ValueError:
                continue
            return candidate
    return peer or "unknown"


def limit_for(scope: str) -> int:
    return _limits().get(scope, _limits()["default"])


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
