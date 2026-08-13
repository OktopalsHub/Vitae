from __future__ import annotations

from collections.abc import Generator
from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import database_url, ensure_dirs
from app.models import Base

ensure_dirs()

_DB_URL = database_url()
_IS_SQLITE = _DB_URL.startswith("sqlite")
_CONNECT_ARGS = {"check_same_thread": False} if _IS_SQLITE else {}

_engine_kwargs: dict = {
    "connect_args": _CONNECT_ARGS,
    "pool_pre_ping": True,
}
if not _IS_SQLITE:
    _engine_kwargs.update(
        {
            "pool_size": 5,
            "max_overflow": 10,
            "pool_recycle": 1800,
        }
    )

engine = create_engine(_DB_URL, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _rename_user_table_to_users() -> None:
    """Rename legacy fastapi-users table `user` → `users` (Postgres reserved word)."""
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    if "users" in tables or "user" not in tables:
        return
    with engine.begin() as conn:
        conn.execute(text('ALTER TABLE "user" RENAME TO users'))


def _quote_ident(name: str) -> str:
    """Quote SQL identifiers for ALTER TABLE / ADD COLUMN."""
    return '"' + name.replace('"', '""') + '"'


def _ensure_column(table: str, column: str, ddl_type: str) -> None:
    insp = inspect(engine)
    if table not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns(table)}
    if column in cols:
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                f"ALTER TABLE {_quote_ident(table)} "
                f"ADD COLUMN {_quote_ident(column)} {ddl_type}"
            )
        )


def migrate_schema() -> None:
    """Additive column patches for SQLite/Postgres without Alembic."""
    _ensure_column("users", "role", "VARCHAR(32) DEFAULT 'basic'")
    _ensure_column("users", "active_profile_id", "INTEGER")
    _ensure_column("job_listings", "closed_at", "TIMESTAMP")
    _ensure_column(
        "job_listings",
        "is_active",
        "BOOLEAN DEFAULT TRUE" if engine.dialect.name != "sqlite" else "BOOLEAN DEFAULT 1",
    )
    _ensure_column("job_listings", "last_seen_at", "TIMESTAMP")
    _ensure_column("job_listings", "public_id", "VARCHAR(32) DEFAULT ''")
    _ensure_column("user_jobs", "profile_id", "INTEGER")
    _ensure_column("apply_drafts", "profile_id", "INTEGER")
    _ensure_column("profiles", "archived_at", "TIMESTAMP")
    _ensure_column("profiles", "free_unlocked_json", "TEXT DEFAULT '[]'")
    _backfill_listing_public_ids()
    _backfill_user_roles()
    _migrate_profiles_from_legacy()
    _migrate_user_billing_to_profile_billing()
    _migrate_overlay_uniques_to_profile()
    _ensure_perf_indexes()


def _ensure_perf_indexes() -> None:
    """Composite indexes that help catalogue browse / sync close paths."""
    statements = [
        "CREATE INDEX IF NOT EXISTS ix_job_listings_active_visibility "
        "ON job_listings (is_active, visibility)",
        "CREATE INDEX IF NOT EXISTS ix_job_listings_scope_active_seen "
        "ON job_listings (scope_key, is_active, last_seen_at)",
        "CREATE INDEX IF NOT EXISTS ix_listing_match_scores_profile_fp_score "
        "ON listing_match_scores (profile_id, fingerprint, match_score)",
    ]
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for stmt in statements:
            if "listing_match_scores" in stmt and "listing_match_scores" not in tables:
                continue
            if "job_listings" in stmt and "job_listings" not in tables:
                continue
            conn.execute(text(stmt))


def _backfill_listing_public_ids() -> None:
    """Assign unguessable public_id tokens to existing catalogue rows."""
    import secrets

    insp = inspect(engine)
    if "job_listings" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("job_listings")}
    if "public_id" not in cols:
        return
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id FROM job_listings
                WHERE public_id IS NULL OR public_id = ''
                """
            )
        ).fetchall()
        for (listing_id,) in rows:
            token = secrets.token_urlsafe(16)[:22]
            conn.execute(
                text("UPDATE job_listings SET public_id = :pid WHERE id = :id"),
                {"pid": token, "id": listing_id},
            )

def _ensure_schema_meta_table(conn: Any) -> None:
    """Create a lightweight one-time migration flag table if it doesn't exist."""
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key VARCHAR(128) PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )


def _migration_applied(conn: Any, key: str) -> bool:
    try:
        row = conn.execute(
            text("SELECT 1 FROM schema_meta WHERE key = :k"), {"k": key}
        ).fetchone()
        return row is not None
    except Exception:
        return False


def _record_migration(conn: Any, key: str) -> None:
    try:
        conn.execute(
            text(
                "INSERT INTO schema_meta (key) VALUES (:k) "
                "ON CONFLICT (key) DO NOTHING"
                if engine.dialect.name != "sqlite"
                else "INSERT OR IGNORE INTO schema_meta (key) VALUES (:k)"
            ),
            {"k": key},
        )
    except Exception:
        pass


def _backfill_user_roles() -> None:
    """Normalize roles to basic / admin / super_admin (role is source of truth).

    The mass SET is_superuser=false / SET is_verified=true statements are gated
    behind a one-time migration flag so they only run on the first deployment and
    never again — preventing accidental privilege-strip on every restart.
    """
    insp = inspect(engine)
    if "users" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("users")}
    if "role" not in cols:
        return
    with engine.begin() as conn:
        _ensure_schema_meta_table(conn)
        # Role normalisation is safe to run on every boot (idempotent).
        conn.execute(
            text(
                """
                UPDATE users
                SET role = 'super_admin'
                WHERE COALESCE(role, '') IN ('', 'user', 'basic')
                  AND is_superuser = true
                """
            )
        )
        conn.execute(
            text(
                """
                UPDATE users
                SET role = 'basic'
                WHERE role IS NULL OR role = '' OR role = 'user'
                """
            )
        )
        # Mass privilege-strip — runs ONCE only.
        if not _migration_applied(conn, "privilege_strip_v1"):
            conn.execute(text("UPDATE users SET is_superuser = false"))
            conn.execute(text("UPDATE users SET is_verified = true"))
            _record_migration(conn, "privilege_strip_v1")


def _migrate_profiles_from_legacy() -> None:
    """Copy legacy user_profiles → profiles and wire active_profile_id / profile_id FKs."""
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    if "profiles" not in tables:
        return

    with engine.begin() as conn:
        if "user_profiles" in tables:
            # Postgres: boolean COALESCE needs false, not 0 (SQLite accepts either).
            confirmed_default = (
                "false" if engine.dialect.name != "sqlite" else "0"
            )
            conn.execute(
                text(
                    f"""
                    INSERT INTO profiles (
                        user_id, label, full_name, email, phone, linkedin, github, website,
                        location_preference, years_experience, work_authorization,
                        salary_expectation, earliest_start, note, master_cv_path,
                        profile_json, career_facts_json, profile_confirmed,
                        created_at, updated_at
                    )
                    SELECT
                        up.user_id, 'Default', up.full_name, up.email, up.phone, up.linkedin,
                        up.github, up.website, up.location_preference, up.years_experience,
                        up.work_authorization, up.salary_expectation, up.earliest_start,
                        up.note, up.master_cv_path, up.profile_json, up.career_facts_json,
                        COALESCE(up.profile_confirmed, {confirmed_default}),
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    FROM user_profiles up
                    WHERE NOT EXISTS (
                        SELECT 1 FROM profiles p WHERE p.user_id = up.user_id
                    )
                    """
                )
            )

        # Wire active_profile_id (SQLite-safe: min profile id per user)
        if "users" in tables:
            conn.execute(
                text(
                    """
                    UPDATE users
                    SET active_profile_id = (
                        SELECT MIN(p.id) FROM profiles p WHERE p.user_id = users.id
                    )
                    WHERE active_profile_id IS NULL
                    """
                )
            )

        if "user_jobs" in tables and any(
            c["name"] == "profile_id" for c in insp.get_columns("user_jobs")
        ):
            conn.execute(
                text(
                    """
                    UPDATE user_jobs
                    SET profile_id = (
                        SELECT MIN(p.id) FROM profiles p WHERE p.user_id = user_jobs.user_id
                    )
                    WHERE profile_id IS NULL
                    """
                )
            )

        if "apply_drafts" in tables and any(
            c["name"] == "profile_id" for c in insp.get_columns("apply_drafts")
        ):
            conn.execute(
                text(
                    """
                    UPDATE apply_drafts
                    SET profile_id = (
                        SELECT MIN(p.id) FROM profiles p WHERE p.user_id = apply_drafts.user_id
                    )
                    WHERE profile_id IS NULL
                    """
                )
            )


def _migrate_user_billing_to_profile_billing() -> None:
    """Copy legacy account billing onto each user's Default profile billing row."""
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    if "profile_billing" not in tables:
        return

    with engine.begin() as conn:
        # Ensure every profile has a billing row (empty plan if never paid).
        conn.execute(
            text(
                """
                INSERT INTO profile_billing (
                    profile_id, user_id, plan, billing_region, llm_provider,
                    llm_key_encrypted, bachs_customer_id, bachs_subscription_id,
                    subscription_status, last_checkout_id, updated_at
                )
                SELECT
                    p.id, p.user_id, 'none', 'ng', 'openai',
                    '', '', '',
                    '', '', CURRENT_TIMESTAMP
                FROM profiles p
                WHERE NOT EXISTS (
                    SELECT 1 FROM profile_billing b WHERE b.profile_id = p.id
                )
                """
            )
        )

        if "user_billing" not in tables:
            return

        # Prefer the user's active profile; else MIN(profile id).
        conn.execute(
            text(
                """
                UPDATE profile_billing
                SET
                    plan = ub.plan,
                    billing_region = ub.billing_region,
                    llm_provider = ub.llm_provider,
                    llm_key_encrypted = ub.llm_key_encrypted,
                    bachs_customer_id = ub.bachs_customer_id,
                    bachs_subscription_id = ub.bachs_subscription_id,
                    subscription_status = ub.subscription_status,
                    last_checkout_id = ub.last_checkout_id,
                    updated_at = CURRENT_TIMESTAMP
                FROM user_billing ub
                WHERE profile_billing.user_id = ub.user_id
                  AND profile_billing.profile_id = (
                      SELECT COALESCE(
                          (SELECT active_profile_id FROM users u WHERE u.id = ub.user_id),
                          (SELECT MIN(p.id) FROM profiles p WHERE p.user_id = ub.user_id)
                      )
                  )
                """
            )
            if engine.dialect.name != "sqlite"
            else text(
                """
                UPDATE profile_billing
                SET
                    plan = (
                        SELECT ub.plan FROM user_billing ub
                        WHERE ub.user_id = profile_billing.user_id
                    ),
                    billing_region = (
                        SELECT ub.billing_region FROM user_billing ub
                        WHERE ub.user_id = profile_billing.user_id
                    ),
                    llm_provider = (
                        SELECT ub.llm_provider FROM user_billing ub
                        WHERE ub.user_id = profile_billing.user_id
                    ),
                    llm_key_encrypted = (
                        SELECT ub.llm_key_encrypted FROM user_billing ub
                        WHERE ub.user_id = profile_billing.user_id
                    ),
                    bachs_customer_id = (
                        SELECT ub.bachs_customer_id FROM user_billing ub
                        WHERE ub.user_id = profile_billing.user_id
                    ),
                    bachs_subscription_id = (
                        SELECT ub.bachs_subscription_id FROM user_billing ub
                        WHERE ub.user_id = profile_billing.user_id
                    ),
                    subscription_status = (
                        SELECT ub.subscription_status FROM user_billing ub
                        WHERE ub.user_id = profile_billing.user_id
                    ),
                    last_checkout_id = (
                        SELECT ub.last_checkout_id FROM user_billing ub
                        WHERE ub.user_id = profile_billing.user_id
                    ),
                    updated_at = CURRENT_TIMESTAMP
                WHERE profile_id = (
                    SELECT COALESCE(
                        (SELECT active_profile_id FROM users u WHERE u.id = profile_billing.user_id),
                        (SELECT MIN(p.id) FROM profiles p WHERE p.user_id = profile_billing.user_id)
                    )
                )
                AND EXISTS (
                    SELECT 1 FROM user_billing ub WHERE ub.user_id = profile_billing.user_id
                )
                """
            )
        )


def _index_names(table: str) -> set[str]:
    insp = inspect(engine)
    if table not in insp.get_table_names():
        return set()
    names: set[str] = set()
    for ix in insp.get_indexes(table) or []:
        if ix.get("name"):
            names.add(ix["name"])
    # SQLite unique constraints often show up as indexes; also check unique constraints.
    try:
        for uc in insp.get_unique_constraints(table) or []:
            if uc.get("name"):
                names.add(uc["name"])
    except NotImplementedError:
        pass
    return names


def _migrate_overlay_uniques_to_profile() -> None:
    """Rebuild overlays so uniqueness is (profile_id, listing_id), not (user_id, listing_id)."""
    dialect = engine.dialect.name
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    if "user_jobs" not in tables:
        return

    # New installs already have the correct unique; skip rebuild when present.
    names = _index_names("user_jobs")
    if "uq_profile_listing" in names and "uq_user_listing" not in names:
        # Still ensure drafts unique if needed
        pass
    elif dialect == "sqlite":
        with engine.begin() as conn:
            # Rebuild user_jobs
            conn.execute(text("PRAGMA foreign_keys=OFF"))
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS user_jobs__pf (
                        id INTEGER NOT NULL PRIMARY KEY,
                        user_id CHAR(32) NOT NULL,
                        profile_id INTEGER,
                        listing_id INTEGER NOT NULL,
                        match_score FLOAT NOT NULL,
                        match_reasons TEXT NOT NULL,
                        status VARCHAR(32) NOT NULL,
                        output_dir VARCHAR(512),
                        scored_at DATETIME,
                        created_at DATETIME NOT NULL,
                        updated_at DATETIME NOT NULL,
                        CONSTRAINT uq_profile_listing UNIQUE (profile_id, listing_id),
                        FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
                        FOREIGN KEY(profile_id) REFERENCES profiles (id) ON DELETE CASCADE,
                        FOREIGN KEY(listing_id) REFERENCES job_listings (id) ON DELETE CASCADE
                    )
                    """
                )
            )
            # Only rebuild if old constraint still dominates (table exists without new unique copy)
            has_new = conn.execute(
                text(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='user_jobs' "
                    "AND sql LIKE '%uq_profile_listing%'"
                )
            ).fetchone()
            if not has_new:
                conn.execute(text("DELETE FROM user_jobs__pf"))
                conn.execute(
                    text(
                        """
                        INSERT INTO user_jobs__pf (
                            id, user_id, profile_id, listing_id, match_score, match_reasons,
                            status, output_dir, scored_at, created_at, updated_at
                        )
                        SELECT
                            id, user_id, profile_id, listing_id, match_score, match_reasons,
                            status, output_dir, scored_at, created_at, updated_at
                        FROM user_jobs
                        """
                    )
                )
                conn.execute(text("DROP TABLE user_jobs"))
                conn.execute(text("ALTER TABLE user_jobs__pf RENAME TO user_jobs"))
            else:
                conn.execute(text("DROP TABLE IF EXISTS user_jobs__pf"))

            if "apply_drafts" in tables:
                has_draft_new = conn.execute(
                    text(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='apply_drafts' "
                        "AND sql LIKE '%uq_profile_draft_listing%'"
                    )
                ).fetchone()
                if not has_draft_new:
                    conn.execute(
                        text(
                            """
                            CREATE TABLE apply_drafts__pf (
                                id INTEGER NOT NULL PRIMARY KEY,
                                user_id CHAR(32) NOT NULL,
                                profile_id INTEGER,
                                listing_id INTEGER NOT NULL,
                                cover_blurb TEXT NOT NULL,
                                answers_json TEXT NOT NULL,
                                updated_at DATETIME NOT NULL,
                                CONSTRAINT uq_profile_draft_listing UNIQUE (profile_id, listing_id),
                                FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
                                FOREIGN KEY(profile_id) REFERENCES profiles (id) ON DELETE CASCADE,
                                FOREIGN KEY(listing_id) REFERENCES job_listings (id) ON DELETE CASCADE
                            )
                            """
                        )
                    )
                    conn.execute(
                        text(
                            """
                            INSERT INTO apply_drafts__pf (
                                id, user_id, profile_id, listing_id, cover_blurb, answers_json, updated_at
                            )
                            SELECT
                                id, user_id, profile_id, listing_id, cover_blurb, answers_json, updated_at
                            FROM apply_drafts
                            """
                        )
                    )
                    conn.execute(text("DROP TABLE apply_drafts"))
                    conn.execute(text("ALTER TABLE apply_drafts__pf RENAME TO apply_drafts"))
            conn.execute(text("PRAGMA foreign_keys=ON"))
    else:
        # Postgres: create new unique indexes if missing (old ones remain but new apps use profile filter).
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_profile_listing "
                    "ON user_jobs (profile_id, listing_id)"
                )
            )
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_profile_draft_listing "
                    "ON apply_drafts (profile_id, listing_id)"
                )
            )


def init_db() -> None:
    # Rename before create_all so we don't create an empty `users` beside legacy `user`.
    _rename_user_table_to_users()
    Base.metadata.create_all(bind=engine)
    migrate_schema()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def uses_sqlite() -> bool:
    return database_url().startswith("sqlite")
