# Phase 22: Data Durability

## Goal

Make user-generated CV artifacts and production database backups survive application container replacement.

## Object storage

Vitae now uses an object-storage abstraction in `app/storage.py`.

### Local development

```env
OBJECT_STORAGE_BACKEND=local
```

Files are stored under `data/objects`.

### Production

Use an Cloudflare R2 bucket. The production Docker image installs the optional `requirements-s3.txt` package set:

```bash
uv pip install -r requirements-s3.txt
```

Use an S3-compatible bucket:

```env
OBJECT_STORAGE_BACKEND=s3
OBJECT_STORAGE_BUCKET=vitae-production
OBJECT_STORAGE_REGION=...
OBJECT_STORAGE_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com
```

Cloudflare R2 uses the S3-compatible API. Set `OBJECT_STORAGE_ENDPOINT` to the R2 S3 API endpoint and provide `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` for the R2 API token.

Generated resume artifacts store a logical object key in the database. Downloads first read the durable artifact record and fall back to the legacy local file path for older generations.

## Database backups

Use `scripts/backup-db.sh` with a PostgreSQL connection string:

```bash
DATABASE_URL='postgresql://...' BACKUP_DIR=./backups ./scripts/backup-db.sh
```

The script creates PostgreSQL custom-format dumps.

Verify an archive before relying on it:

```bash
DATABASE_URL='postgresql://...' BACKUP_FILE=./backups/vitae-....dump ./scripts/verify-db-backup.sh
```

The verification command checks that PostgreSQL can read the archive. It does not replace a real restore drill.

## Production backup policy

The deployment environment should:

1. Run automated PostgreSQL backups.
2. Store backups outside the application container and database host.
3. Encrypt backups at rest.
4. Keep multiple recovery points.
5. Test restores on a separate database on a scheduled basis.
6. Monitor backup age and failed backup jobs.
7. Enable point-in-time recovery when the managed PostgreSQL provider supports it.

## Artifact policy

- Do not use the container filesystem as the production source of truth.
- Keep generated artifacts immutable.
- Store the checksum already recorded in `resume_artifacts`.
- Use separate object-storage credentials with only the bucket permissions Vitae needs.
- Configure lifecycle rules according to the product retention policy.

## Acceptance criteria

- [x] Storage backend is behind a small application interface.
- [x] Local storage remains available for development.
- [x] S3-compatible storage is supported.
- [x] Generated resume artifacts persist through the storage interface.
- [x] Downloads can read artifacts without the original web container filesystem.
- [x] PostgreSQL backup command exists.
- [x] Backup archive verification command exists.
- [x] Production backup and restore requirements are documented.

## Operational limitation

The repository provides backup and verification commands, but the actual production backup schedule, bucket, retention policy, credentials, and restore drills must be configured in the deployment environment.
