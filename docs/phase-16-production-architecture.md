# Phase 16: Production Architecture

## Goal

Make Vitae deployable as separate production services with clear ownership of web traffic, background work, scheduling, and database migrations.

## Service topology

### Web

- Runs FastAPI only.
- Does not execute catalogue or matching work.
- Does not run the periodic scheduler in production.
- Exposes /health for process health.
- Exposes /health/ready for database readiness.
- Uses one worker by default.
- Can scale horizontally when the deployment platform supports it.

### Workers

The production compose file separates the durable queues:

- worker-default processes general jobs.
- worker-catalogue processes job catalogue sync.
- worker-matching processes user matching refresh jobs.

All workers use the same immutable application image.

### Scheduler

- Runs periodic enqueue loops only.
- Enqueues catalogue sync and user rank refresh jobs.
- Is a separate service so multiple web replicas do not create duplicate periodic schedulers.

### Database migrations

Migrations are an explicit deployment step:

1. Build the release image.
2. Run ./scripts/migrate.sh against the target database.
3. Start or roll the web service.
4. Start or roll workers and the scheduler.
5. Verify /health/ready.

Do not run migrations automatically from the web process. This avoids concurrent migration races when several replicas start together.

## Container rules

- Python 3.12 is pinned by the project.
- Dependencies are installed from uv.lock with --frozen.
- Runtime containers run as an unprivileged UID.
- Tests, docs, local databases, .env files and Git metadata are excluded from the image.
- Web, workers and scheduler use the same application image.
- WEB_WORKERS=1 is the default.

## Configuration

Production secrets stay outside the image.

Required application secrets include:

- SECRET_KEY
- FERNET_SECRET_KEY
- DATABASE_URL

Provider credentials remain environment variables, including:

- FIRECRAWL_API_KEY
- LLM provider keys
- OAuth credentials
- Bachs credentials

FIRECRAWL_API_KEY remains optional. Firecrawl must not be required for the application to boot.

## Health checks

GET /health confirms that the process is serving.

GET /health/ready checks the database migration state. A failed migration check returns a server error so a deployment platform does not route traffic to an incompatible application.

## Graceful shutdown

The web lifespan cancels periodic tasks during shutdown. In production the web sets RUN_SCHEDULER=0, so periodic scheduling belongs only to the scheduler service.

The worker handles SIGTERM and stops claiming new work while allowing the current job to finish.

Container stop grace periods are:

- web: 30 seconds
- scheduler: 30 seconds
- workers: 60 seconds

## Deployment and rollback

Recommended release flow:

1. Build and tag one immutable image.
2. Run the test suite in CI.
3. Run alembic upgrade head as a controlled migration step.
4. Deploy the web service.
5. Verify readiness.
6. Deploy workers and scheduler using the same image tag.
7. Monitor errors and worker failures.
8. Roll back application containers if needed.

Database migrations must be backward compatible with the previous application during rolling deployments. Destructive schema changes should use an expand/contract migration:

1. add new schema;
2. deploy code that supports both forms;
3. backfill;
4. switch reads/writes;
5. remove old schema in a later release.

## Backups and disaster recovery

The production database provider must have automated backups and point-in-time recovery enabled.

Before destructive database operations:

- verify a recent backup exists;
- record the migration/image version;
- test restore procedures in a non-production environment.

Application uploads should use durable object storage before production scale. Local container storage is not a backup strategy.

## Acceptance criteria

- [x] Web container is defined.
- [x] Worker containers are defined.
- [x] Scheduler is a separate service.
- [x] Production migrations are explicit.
- [x] Containers run without root.
- [x] Dependency lockfile is used during image build.
- [x] Web readiness checks migration state.
- [x] Graceful shutdown is documented and configured.
- [x] Production deployment topology is documented.
- [x] Rollback and migration compatibility rules are documented.
- [ ] CI validates the production image build.
- [ ] A target cloud deployment is configured.
- [ ] Automated database backup/restore is configured.
