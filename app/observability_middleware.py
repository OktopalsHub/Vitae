from __future__ import annotations

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware

from app.observability import new_request_id, request_id_ctx

log = logging.getLogger("vitae.http")

class ObservabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        incoming_request_id = (request.headers.get("X-Request-ID") or "").strip()\n        request_id = incoming_request_id[:128] if incoming_request_id else new_request_id()
        request.state.request_id = request_id
        token = request_id_ctx.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            elapsed = round((time.perf_counter() - started) * 1000, 2)
            log.exception("request failed method=%s path=%s duration_ms=%s", request.method, request.url.path, elapsed)
            raise
        finally:
            request_id_ctx.reset(token)
        elapsed = round((time.perf_counter() - started) * 1000, 2)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-Ms"] = str(elapsed)
        log.info("request completed method=%s path=%s status=%s duration_ms=%s", request.method, request.url.path, response.status_code, elapsed)
        return response
