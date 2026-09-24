# Phase 14: Observability

## Goals

Make production behaviour visible without logging secrets or user content.

## Implemented

- Structured JSON application logs.
- Request correlation IDs using `X-Request-ID`.
- Response includes `X-Request-ID` and request duration.
- HTTP request completion/failure logging.
- Liveness endpoint: `GET /health`.
- Readiness endpoint: `GET /health/ready`, including database migration validation.
- Worker execution counters and p95 latency snapshot in-process.
- Worker logs include job ID, kind and duration.
- Worker failures are logged with exception details while job payloads are not logged.

## Production use

The request ID should be propagated through the reverse proxy/API gateway and returned to clients. This makes it possible to correlate a user report with application logs.

Run the web process with normal application logging:

```bash
LOG_LEVEL=INFO uv run uvicorn app.main:app
```

Run workers separately:

```bash
LOG_LEVEL=INFO uv run vitae-worker
```

## Security rule

Do not add request bodies, authorization headers, cookies, CV contents, prompts, LLM responses, billing payloads or provider secrets to normal logs.

## Next observability work

A later production-hardening step can export these metrics to Prometheus/OpenTelemetry and persist worker metrics centrally. The current implementation deliberately keeps the application dependency-light.
