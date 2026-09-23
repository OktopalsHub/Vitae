# Phase 1 and 2 security and schema operations

## Database

Vitae no longer mutates the schema during application startup.

New or upgraded environments must run:

```bash
alembic upgrade head
```

Check the current revision:

```bash
alembic current
```

For an existing database created before Alembic was introduced:

1. Verify the existing schema matches the pre-normalization application schema.
2. Stamp it at the baseline revision:

```bash
alembic stamp 0001_baseline
```

3. Apply the new migrations:

```bash
alembic upgrade head
```

Do not use `Base.metadata.create_all()` in production. It is only used by the isolated test fixture.

## Security configuration

Recommended production settings:

```env
REDIS_URL=redis://...
TRUSTED_PROXY_IPS=10.0.0.0/8
RATE_LIMIT_AUTH=10
RATE_LIMIT_PASTE=20
RATE_LIMIT_AI=15
RATE_LIMIT_DEFAULT=60

SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=...
SMTP_PASSWORD=...
SMTP_FROM=noreply@example.com
SMTP_STARTTLS=1
```

Only add addresses or CIDRs that are actually trusted reverse proxies. Never set `TRUSTED_PROXY_IPS` to an unrestricted network unless that network is controlled by the deployment.

Redis is used for atomic distributed rate limiting. The local limiter remains a continuity fallback if Redis is temporarily unavailable.

## Phase 2 data model

The normalized profile foundation is now represented by:

- `profile_experiences`
- `profile_education`
- `profile_skills`
- `profile_projects`
- `profile_certifications`
- `profile_preferences`
- `documents`
- `resume_versions`

The old JSON fields remain temporarily for compatibility. Migration `0004_backfill_profile` copies the existing structured portions into the normalized tables. The JSON fields should only be removed after all reads and writes have moved to the normalized models.

## Deployment rule

Migration execution is a deployment concern, not an application-startup concern.

A non-test application refuses to start when its database is not at the Alembic head. This prevents a new application version from silently changing production schema or serving traffic against an incompatible database.
