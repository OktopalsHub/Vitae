# Phase 21: Production Observability

## Goal

Make production behavior measurable without adding a runtime metrics dependency.

## Changes

- Added a protected /metrics endpoint.
- Added Prometheus text exposition for worker job counts and p95 latency.
- Metrics are disabled when METRICS_TOKEN is not configured.
- Metrics access uses a bearer token and constant-time comparison.
- Added regression coverage for metrics output and authorization.
- Documented the operator token configuration.

## Metrics

Current worker metrics include:

- vitae_worker_jobs_total{kind,status}
- vitae_worker_job_latency_p95_ms

The metrics are process-local. Deployments with multiple worker replicas should scrape each replica or aggregate metrics at the infrastructure layer.

## Security

/metrics does not expose anything when no token is configured. When enabled, callers must send an Authorization: Bearer <METRICS_TOKEN> header.

Do not expose the metrics endpoint publicly without network-level controls or a strong operator token.

## Acceptance criteria

- Metrics endpoint is unavailable when not configured.
- Unauthorized metrics requests return 401.
- Authorized requests return Prometheus-compatible text.
- Worker counters and latency remain bounded in memory.
- Existing health endpoints remain unchanged.
