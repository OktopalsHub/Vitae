# Phase 23: Operational Resilience

## Goal

Give operators safe visibility and recovery controls for background work.

## Worker operations

Administrators can query:

`GET /admin/operations/workers`

The response reports:

- queued jobs
- running jobs
- completed jobs
- dead jobs
- running jobs with no heartbeat for 10 minutes

Dead jobs can be manually requeued with:

`POST /admin/operations/workers/{job_id}/retry`

The retry action preserves the original payload. It clears the old lease and makes the job immediately available to a worker.

## Safety

- Both endpoints require an authenticated administrator.
- A non-dead job cannot be manually retried.
- Requeued jobs keep their original kind and payload.
- Worker lease recovery remains automatic. The admin action is for deliberate operator recovery.
- No worker credentials or payload contents are exposed by the queue health endpoint.

## R2

Phase 22 is configured for Cloudflare R2 through the S3-compatible storage interface. Production should use:

```env
OBJECT_STORAGE_BACKEND=s3
OBJECT_STORAGE_BUCKET=vitae-production
OBJECT_STORAGE_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
```

## Acceptance criteria

- [x] Operators can see queue health.
- [x] Stale running jobs are visible.
- [x] Dead jobs can be deliberately requeued.
- [x] Recovery controls are admin-only.
- [x] R2 configuration is documented.
