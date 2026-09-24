"""Fixed-window rate limiting with optional Redis coordination.

Local mode is useful for development and single-process deployments.
Set RATE_LIMIT_BACKEND=redis and REDIS_URL in production when multiple web
replicas must share the same limits.
"""

from __future__ import annotations

import threading
import time
from functools import lru_cache
from typing import Callable

from fastapi import Request

from app.config import get_settings

_lock = threading.Lock()
_buckets: dict[str, tuple[float, int]] = {}

_LIMIT_AUTH = 10
_LIMIT_PASTE = 20
_LIMIT_AI = 15
_LIMIT_DEFAULT = 60

_RATE_LIMIT_LUA = """
local current = redis.call("INCR", KEYS[1])
if current == 1 then
  redis.call("EXPIRE", KEYS[1], ARGV[1])
end
return current
"""


class RateLimitExceeded(Exception):
    def __init__(self, message: str = "Too many requests. Try again shortly.", *, path: str = "/"):
        self.message = message
        self.path = path or "/"
        super().__init__(message)


@lru_cache
def _redis_client():
    settings = get_settings()
    if (settings.rate_limit_backend or "").strip().lower() != "redis":
        return None
    url = (settings.redis_url or "").strip()
    if not url:
        return None
    try:
        import redis
        return redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=1, socket_timeout=1)
    except Exception:
        return None


def reset_rate_limits() -> None:
    with _lock:
        _buckets.clear()


def _check_local(key: str, *, limit: int, window_seconds: int, path: str) -> None:
    now = time.time()
    with _lock:
        start, count = _buckets.get(key, (now, 0))
        if now - start >= window_seconds:
            start, count = now, 0
        count += 1
        _buckets[key] = (start, count)
        if count > limit:
            raise RateLimitExceeded(path=path)


def check_rate_limit(
    key: str,
    *,
    limit: int,
    window_seconds: int = 60,
    path: str = "/",
) -> None:
    if limit <= 0:
        return

    client = _redis_client()
    if client is not None:
        try:
            count = int(
                client.eval(
                    _RATE_LIMIT_LUA,
                    1,
                    "vitae:rl:" + key,
                    window_seconds,
                )
            )
            if count > limit:
                raise RateLimitExceeded(path=path)
            return
        except RateLimitExceeded:
            raise
        except Exception:
            pass

    _check_local(key, limit=limit, window_seconds=window_seconds, path=path)


def _client_key(request: Request, bucket: str) -> str:
    user_id = getattr(getattr(request, "state", None), "user_id", None)
    if user_id:
        return f"{bucket}:user:{user_id}"
    client_ip = request.client.host if request.client else "unknown"
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
    if user_id:
        key = f"{bucket}:user:{user_id}"
    elif request is not None:
        key = _client_key(request, bucket)
    else:
        key = f"{bucket}:anonymous"
    check_rate_limit(key, limit=limit_for(bucket), path=redirect_path)


def rate_limit(bucket: str, *, redirect_path: str = "/") -> Callable:
    """Return a FastAPI dependency that applies the named rate-limit bucket."""

    async def dependency(request: Request) -> None:
        enforce(bucket, request=request, redirect_path=redirect_path)

    return dependency
