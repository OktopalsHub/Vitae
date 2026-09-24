# Phases 11-13

## Phase 11: Durable background processing

Heavy work is no longer required to run inside the web request or FastAPI scheduler process.

Implemented:
- Durable `worker_jobs` table.
- Queue, lease, retry, backoff and dead-letter state.
- Worker runner: `vitae-worker`.
- Scheduler now enqueues catalogue sync and matching refresh work.
- Auto-apply preparation is queued per item.
- Worker tasks use short-lived database sessions.
- Auto-apply preparation is idempotent.
- Explicit approval remains required before external submission.

Run locally:

```bash
uv run vitae-worker
```

Use separate workers for higher throughput:

```bash
uv run vitae-worker --queue matching
uv run vitae-worker --queue catalogue
uv run vitae-worker --queue auto-apply
```

## Phase 12: Billing reliability

Implemented:
- Append-only `billing_events` receipt table.
- Provider + event ID uniqueness for webhook idempotency.
- Event payload hash for integrity checks.
- Processed/error state.
- Append-only subscription status transition history.
- Existing Bachs entitlement logic remains the source of access state.

The webhook can safely receive the same provider event more than once without applying the entitlement transition twice.

## Phase 13: Search and performance

Implemented:
- Database-first job search.
- Search over title, company, location and description.
- Database filtering for location, remote type, employment type and experience level.
- Match-score cache is used for ordering and threshold filtering.
- Pagination is performed in SQL with a bounded page size.
- New indexes support active catalogue filters and posted-date ordering.
- Cold profiles no longer trigger a full rescore inside the /jobs request. A durable matching job is queued instead.

### Important operational rule

The web application and worker process should run separately in production. The web process serves requests. Worker processes consume `worker_jobs`.
