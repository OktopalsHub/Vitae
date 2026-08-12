from __future__ import annotations

import pytest
from fastapi import Request

from app.rate_limit import (
    RateLimitExceeded,
    check_rate_limit,
    enforce,
    reset_rate_limits,
)


@pytest.fixture(autouse=True)
def _clear_limits():
    reset_rate_limits()
    yield
    reset_rate_limits()


def test_check_rate_limit_allows_under_cap():
    for _ in range(3):
        check_rate_limit("t1", limit=3, window_seconds=60)


def test_check_rate_limit_blocks_over_cap():
    for _ in range(2):
        check_rate_limit("t2", limit=2, window_seconds=60)
    with pytest.raises(RateLimitExceeded):
        check_rate_limit("t2", limit=2, window_seconds=60)


def test_limit_zero_disables():
    for _ in range(20):
        check_rate_limit("off", limit=0, window_seconds=60)


def test_enforce_sets_redirect_path():
    from app.rate_limit import limit_for

    reset_rate_limits()
    limit = limit_for("auth")
    for _ in range(limit):
        enforce("auth", user_id="u2", redirect_path="/login")
    with pytest.raises(RateLimitExceeded) as ei:
        enforce("auth", user_id="u2", redirect_path="/login")
    assert ei.value.path == "/login"
