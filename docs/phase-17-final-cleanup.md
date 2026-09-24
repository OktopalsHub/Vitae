# Phase 17: Final Cleanup

## Goal

Remove misleading implementation remnants, align the documentation with the current architecture, and leave a clean production handoff.

## Completed

- [x] Removed broken Redis references from the rate limiter.
- [x] Kept the application rate limiter explicitly single-process.
- [x] Preserved the existing rate-limit helper API used by tests and routes.
- [x] Added regression coverage for rate-limit windows and limits.
- [x] Updated the README architecture diagram.
- [x] Documented web, worker, and scheduler separation.
- [x] Documented Firecrawl as optional open-web discovery.
- [x] Documented explicit Alembic migrations.
- [x] Removed obsolete generated README claims.
- [x] Added a final production handoff checklist.

## Production handoff checklist

- [ ] Set APP_ENV=production.
- [ ] Use PostgreSQL.
- [ ] Set SECRET_KEY and FERNET_SECRET_KEY.
- [ ] Configure OAuth credentials if OAuth is enabled.
- [ ] Configure billing credentials before enabling billing.
- [ ] Configure FIRECRAWL_API_KEY only when open-web discovery is wanted.
- [ ] Run alembic upgrade head before starting services.
- [ ] Run web, workers, and scheduler as separate services.
- [ ] Put a distributed rate limiter or API gateway in front of multiple web replicas.
- [ ] Enable database backups and point-in-time recovery.
- [ ] Move generated CV artifacts to durable object storage.
- [ ] Run CI and the production Docker build.
- [ ] Verify /health/ready after deployment.

## Out of scope

- Choosing a cloud provider.
- Production credentials.
- Automated infrastructure provisioning.
- Payment provider changes.
- New matching or AI behavior.
