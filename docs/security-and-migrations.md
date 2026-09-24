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


## Phase 5: Job data platform

The job catalogue now keeps source health and source-specific provenance separate from the canonical job listing.

- `job_sources` tracks each configured source, last success/failure, consecutive failures, and fetch counts.
- `job_source_records` stores the source external ID and raw normalized payload that produced a listing.
- `job_listings` now stores normalized location, employment type, remote type, experience level, salary range/currency, source timestamps, a canonical key, and a source key.
- A failed source does not make the whole catalogue sync fail and does not cause that source's jobs to be closed.
- Empty successful results are treated differently from source errors.
- Source records are the provenance boundary. The canonical listing remains the user-facing job record.

Run the new migration with:

```bash
alembic upgrade head
```

For a source outage, investigate the source health row before treating missing jobs as closed. A sync can finish with `failed_sources > 0`; this means the catalogue was partially refreshed and stale jobs from failed sources were intentionally preserved.


## Phase 6: Matching platform

Matching now has an explicit algorithm version (v2) and a structured scoring contract. Persisted match scores store the algorithm version, fingerprint, human-readable reasons, and a machine-readable breakdown containing positive signals, penalties, skill hits/gaps, stack signals, and a connection summary.

The profile/config fingerprint includes the matching algorithm version. Changing the algorithm therefore invalidates old cached scores instead of silently serving results from an older scorer.

The matching result is deterministic and explainable. It does not use an opaque score without retaining the signals that produced it.


## Phase 7: AI platform

All application LLM calls pass through the shared `llm_complete` gateway.

The gateway provides:

- provider/model resolution through the canonical provider registry
- bounded provider timeout and retry settings
- a common safety instruction that treats job and user supplied text as untrusted data
- privacy-safe telemetry for provider, model, purpose, prompt version, status, latency, and input/output sizes
- no persistence of prompts, generated text, API keys, or other secret material
- asynchronous telemetry writes so database logging does not block the application event loop

Generated structured data is validated with Pydantic contracts before it is accepted by resume and application-copy workflows.

Prompt versions are explicit for major generation workflows:

- `cv_tailor.v2`
- `application_copy.v2`

If a prompt contract changes, increment its version. This makes later quality and failure analysis traceable to the exact generation contract.

Run:

```bash
alembic upgrade head
```

Migration `0009_llm_observability` creates the `llm_requests` metadata table.

AI safety is a data-boundary control, not a guarantee that generated text is truthful. Vitae still uses post-validation and rule-based fallbacks when model output is missing, malformed, or incomplete.


## Phase 8: Resume generation

Generated resumes are now treated as immutable artifacts rather than temporary files.

Each generation records:

- profile and target listing
- source master CV version
- generator version
- prompt version
- whether the rule-based fallback was used
- output directory
- generated artifact metadata

Each PDF/DOCX artifact records its format, filename, content type, size, storage key, and SHA-256 checksum.

Every generation gets a unique output directory. Generating a new CV no longer deletes the previous generated files.

Local filesystem access is behind `LocalResumeStorage`, which validates storage keys before reading or writing. This gives the application a clean boundary for a future object-storage implementation.

Run:

```bash
alembic upgrade head
```

Migration `0010_resume_artifacts` creates `resume_generations` and `resume_artifacts`.

Resume generation remains synchronous for now. Phase 11 moves this work to durable background workers.


## Phase 9: Application lifecycle

Applications are now a durable record instead of a status flag on `user_jobs`.

Each application is unique per active profile and job listing and stores:

- lifecycle status
- submission channel
- employer application URL or external ID
- the cover note and application answers used for the application
- the tailored resume artifact used, when one exists
- submission timestamp and notes

Application history is append-only in `application_events`. Status changes are validated by an explicit transition map, so terminal states cannot silently move backwards.

The existing `user_jobs.status` field remains for compatibility. Moving an application to `submitted` also moves its matching job overlay to `applied`. Future application UI should use the application record as the source of truth.

Creating an application is idempotent for the same profile and listing. Re-opening the same job does not create duplicate applications.

Run:

```bash
alembic upgrade head
```

Migration `0011_applications` creates `applications` and `application_events`.

The application record stores a snapshot of the application copy. This is intentional: later edits to an Apply Assist draft must not change what was recorded as submitted.

Actual employer submission remains user-controlled. Vitae records that the user marked the application as submitted; it does not claim that an external employer system accepted the submission.


## Phase 10: Controlled auto-apply foundation

Auto-apply is modeled as a durable, user-controlled workflow. A run contains one or more application items and has explicit lifecycle state.

The first implementation deliberately separates **application preparation** from **external submission**:

- A run creates or reuses the user's application records.
- Each item tracks preparation, review, attempts, errors, and submission state.
- Runs can be started, paused, and cancelled.
- Items can be marked for review.
- Marking an item submitted requires an explicit user action.
- Employer URLs and external application IDs are recorded on the application.
- Idempotency keys prevent duplicate batches.
- Ownership is checked for every run and item operation.

No employer website automation or credential storage is introduced in this phase. That belongs behind a dedicated submission adapter and worker boundary so employer-specific behavior cannot bypass application ownership, review, rate limits, or audit requirements.

Migration `0012_auto_apply` creates `auto_apply_runs` and `auto_apply_items`.

Run:

```bash
alembic upgrade head
```
