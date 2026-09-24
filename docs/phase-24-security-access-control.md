# Phase 24 — Production Security & Access Control

## Goal

Harden privileged operations and production configuration before deployment.

## Implemented

- Added append-only admin audit records for privileged actions.
- Role changes and worker-job retries now create audit records.
- Added an admin audit endpoint at GET /admin/operations/audit.
- Existing global CSRF middleware protects the worker retry POST endpoint and other cookie-authenticated state changes.
- Production configuration now validates SECRET_KEY and FERNET_SECRET_KEY, optional METRICS_TOKEN length, Redis configuration, object-storage backend values, HTTPS storage endpoints, and Cloudflare R2 credentials when an R2 endpoint is configured.
- Removed the known public insecure secret from .env.example.
- Audit metadata is limited to operational identifiers and safe values. Secrets, tokens, request bodies, and worker payloads are not recorded.

## Access-control model

- /admin requires an admin user.
- User role management remains super-admin only.
- Worker operations remain admin-only.
- The worker retry endpoint only accepts jobs already in dead state.
- CSRF protection remains global for non-safe methods and uses the vitae_csrf double-submit cookie plus form/header token.

## Production checklist

- Set unique random SECRET_KEY and FERNET_SECRET_KEY.
- If METRICS_TOKEN is enabled, use a random value of at least 32 characters.
- Use RATE_LIMIT_BACKEND=redis and a private REDIS_URL when running multiple web replicas.
- Use OBJECT_STORAGE_BACKEND=s3 with private R2 credentials and a private bucket.
- Use HTTPS for the public application and storage endpoint.
- Run alembic upgrade head before starting application services.
- Review /admin/operations/workers and /admin/operations/audit as operator surfaces.
- Do not put secrets or raw request payloads into audit metadata.

## Acceptance criteria

- Privileged role changes are auditable.
- Manual worker recovery is auditable.
- Admin-only operations remain access-controlled.
- State-changing browser requests remain CSRF-protected.
- Production startup rejects unsafe storage/rate-limit/metrics configuration.
- Example environment configuration contains no usable application secret.
