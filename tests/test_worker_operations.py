from __future__ import annotations

import asyncio
import sys

import pytest


def test_worker_operations_requires_admin(client):
    response = client.get("/admin/operations/workers", follow_redirects=False)
    assert response.status_code in {302, 303}


@pytest.mark.skipif(sys.platform != "win32", reason="Windows event loop policy")
def test_selector_loop_factory_is_usable_by_psycopg():
    """Windows must not hand psycopg a ProactorEventLoop.

    psycopg's async driver raises InterfaceError on ProactorEventLoop, which
    made every register/login request return HTTP 500. uvicorn builds its loop
    with an explicit factory that ignores the event loop policy, so the app
    ships selector_loop_factory for the server to use.
    """
    from app.main import selector_loop_factory

    loop = selector_loop_factory()
    try:
        assert isinstance(loop, asyncio.SelectorEventLoop)
    finally:
        loop.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows event loop policy")
def test_windows_event_loop_policy_is_selector_based():
    import app.main  # noqa: F401  (import applies the policy)

    assert isinstance(
        asyncio.get_event_loop_policy(), asyncio.WindowsSelectorEventLoopPolicy
    )
