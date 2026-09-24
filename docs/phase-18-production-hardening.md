# Phase 18: Production Hardening

## Goal

Make the production handoff safer for horizontally scaled web deployments without forcing Redis on local development.

## Changes

- [x] Add an explicit `RATE_LIMIT_BACKEND` setting.
- [x] Keep local fixed-window limiting as the default.
- [x] Add optional Redis-backed fixed-window limiting for shared limits across web replicas.
- [x] Keep Redis failures from taking the web application down.
- [x] Preserve the existing rate-limit helper API.
- [x] Fix redirect-path propagation when a rate limit is exceeded.
- [x] Document the Redis settings in `.env.example` and the README.
- [x] Add regression coverage for the redirect path.

## Configuration

Local development:

```env
RATE_LIMIT_BACKEND=local
```

Multiple web replicas:

```env
RATE_LIMIT_BACKEND=redis
REDIS_URL=redis://...
```

The Redis limiter uses an atomic increment with a TTL. The key is scoped by rate-limit bucket and user/IP identity.

If Redis is unavailable, the application falls back to the local limiter. This protects availability but means limits are not globally shared during the outage. For stricter security requirements, put a distributed rate limiter or API gateway in front of the application.

## Operational notes

- Redis is optional for single-process deployments.
- Redis must be reachable by every web replica when shared limiting is enabled.
- Do not put secrets directly in `config.yaml`.
- Monitor Redis connectivity and rate-limit fallback events before relying on the limiter for abuse protection.
- The database remains the source of truth for application data and migrations.

## Acceptance criteria

- [x] Existing local rate-limit tests remain valid.
- [x] Redirect paths are preserved on 429 responses.
- [x] Redis mode is opt-in.
- [x] Redis outages do not crash request handling.
- [x] Configuration is documented.