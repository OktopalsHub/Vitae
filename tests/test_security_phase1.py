from __future__ import annotations

from starlette.requests import Request

from app.config import get_settings
from app.rate_limit import client_ip


def _request(*, peer: str, forwarded: str | None = None) -> Request:
    headers = []
    if forwarded is not None:
        headers.append((b"x-forwarded-for", forwarded.encode()))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "client": (peer, 1234),
        "scheme": "http",
        "server": ("testserver", 80),
    }
    return Request(scope)


def test_forwarded_for_is_ignored_from_untrusted_peer():
    request = _request(peer="10.0.0.10", forwarded="203.0.113.10")
    assert client_ip(request) == "10.0.0.10"


def test_forwarded_for_is_used_from_trusted_proxy(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "10.0.0.10")
    get_settings.cache_clear()
    try:
        request = _request(
            peer="10.0.0.10",
            forwarded="203.0.113.10, 10.0.0.11",
        )
        assert client_ip(request) == "10.0.0.11"
    finally:
        get_settings.cache_clear()


def test_security_headers_are_present(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "geolocation=()" in response.headers["permissions-policy"]
