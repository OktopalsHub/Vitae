"""Small fixed-window rate limiter for single-process deployments.

For horizontally scaled production deployments, use an edge/API gateway
rate limit in front of Vitae. This module intentionally has no hidden Redis
dependency.
"""

from __future__ import annotations

import threading
import time

from fastapi import Request

_lock = threading.Lock()
_buckets: dict[str, tuple[float, int]] = {}

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
    with _lock:
        _buckets.clear()


def check_rate_limit(key: str, *, limit: int, window_seconds: int = 60) -> None:
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


def _client_key(request: Request, bucket: str) -> str:
    user_id = getattr(getattr(request, "state", None), "user_id", None)
    if user_id:
        return f"{bucket}:user:{user_id}"
    forwarded = request.headers.get("x-forwarded-for", "")
    client_ip = forwarded.split(",", 1)[0].strip() if forwarded else (
        request.client.host if request.client else "unknown"
    )
    return f"{bucket}:ip:{client_ip}"


def limit_for(bucket: str) -> int:
    return {
        "auth": _LIMIT_AUTH,
        "paste": _LIMIT_PASTE,
        "ai": _LIMIT_AI,
        "default": _LIMIT_DEFAULT,
    }.get(bucket, _LIMIT_DEFAULT)


def enforce(
    bucket: str,
    *,
    request: Request | None = None,
    user_id: str | None = None,
    redirect_path: str = "/",
) -> None:
    limits = {
        "auth": _LIMIT_AUTH,
        "paste": _LIMIT_PASTE,
        "ai": _LIMIT_AI,
        "default": _LIMIT_DEFAULT,
    }
    if user_id:
        key = f"{bucket}:user:{user_id}"
    elif request is not None:
        key = _client_key(request, bucket)
    else:
        key = f"{bucket}:anonymous"
    check_rate_limit(key, limit=limits.get(bucket, _LIMIT_DEFAULT))

