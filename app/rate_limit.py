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

_RATE_LIMIT_LUA = """
local current = redis.call("INCR", KEYS[1])
if current == 1 then redis.call("EXPIRE", KEYS[1], ARGV[1]) end
return current
"""


def _redis():
    return None



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
    """Atomically enforce a fixed-window limit in Redis when configured."""
    if limit <= 0:
        return
    client = _redis()
    if client is not None:
        try:
            count = int(client.eval(_RATE_LIMIT_LUA, 1, "vitae:rl:" + key, window_seconds))
            if count > limit:
                raise RateLimitExceeded()
            return
        except RateLimitExceeded:
            raise
        except Exception:
            # Redis is a control-plane dependency, but an outage must not take the
            # application down. Fall back to the bounded local limiter for continuity.
            pass

    now = time.time()
    with _lock:
        start, count = _buckets.get(key, (now, 0))
        if now - start >= window_seconds:
            start, count = now, 0
        count += 1
        _buckets[key] = (start, count)
        if count > limit:
            raise RateLimitExceeded()



def limit_for(scope: str) -> int:
    return {"auth": _LIMIT_AUTH, "paste": _LIMIT_PASTE, "ai": _LIMIT_AI}.get(scope, _LIMIT_DEFAULT)


def enforce(scope: str, *, request: Request | None = None, user_id: Any = None, redirect_path: str = "/", window_seconds: int = 60) -> None:
    key = f"{scope}:user:{user_id}" if user_id is not None else f"{scope}:ip:{request.client.host if request and request.client else 'unknown'}"
    try:
        check_rate_limit(key, limit=limit_for(scope), window_seconds=window_seconds)
    except RateLimitExceeded as exc:
        exc.path = redirect_path
        raise


def rate_limit(scope: str, *, window_seconds: int = 60, redirect_path: str = "/") -> Callable[..., Any]:
    async def _dependency(request: Request) -> None:
        enforce(scope, request=request, redirect_path=redirect_path, window_seconds=window_seconds)
    return _dependency
