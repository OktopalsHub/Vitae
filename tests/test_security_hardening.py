from __future__ import annotations

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

from app.main import SecurityHeadersMiddleware


def _app() -> Starlette:
    app = Starlette()

    @app.route("/health")
    async def health(request: Request):
        return PlainTextResponse("ok")

    app.add_middleware(SecurityHeadersMiddleware)
    return app


def test_security_headers_are_present():
    with TestClient(_app()) as client:
        response = client.get("/health")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert response.headers["Permissions-Policy"] == "camera=(), microphone=(), geolocation=()"


def test_request_id_is_bounded():
    # The observability middleware truncates externally supplied IDs to 128 chars.
    from app.observability_middleware import ObservabilityMiddleware

    app = Starlette()

    @app.route("/health")
    async def health(request: Request):
        return PlainTextResponse("ok")

    app.add_middleware(ObservabilityMiddleware)

    with TestClient(app) as client:
        response = client.get("/health", headers={"X-Request-ID": "x" * 500})

    assert len(response.headers["X-Request-ID"]) == 128
