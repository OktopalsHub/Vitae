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

