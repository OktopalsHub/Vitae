from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import database_url, ensure_dirs, get_settings, project_path

ensure_dirs()

_DB_URL = database_url()
_IS_SQLITE = _DB_URL.startswith("sqlite")
_CONNECT_ARGS = {"check_same_thread": False} if _IS_SQLITE else {}
_engine_kwargs: dict = {"connect_args": _CONNECT_ARGS, "pool_pre_ping": True}
if not _IS_SQLITE:
    _engine_kwargs.update({"pool_size": 5, "max_overflow": 10, "pool_recycle": 1800})

engine = create_engine(_DB_URL, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)



def assert_database_migrated() -> None:
    """Fail fast when a non-test database has not reached the Alembic head."""
    if (get_settings().app_env or "").strip().lower() in {"test", "testing"}:
        return
    from alembic.config import Config
    from alembic.migration import MigrationContext
    from alembic.script import ScriptDirectory

    cfg = Config(str(project_path("alembic.ini")))
    script = ScriptDirectory.from_config(cfg)
    expected = script.get_current_head()
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        current = context.get_current_heads()
    if current != (expected,):
        raise RuntimeError(
            f"Database migration mismatch: current={current or 'none'}, expected={expected}. "
            "Run 'alembic upgrade head' before starting Vitae."
        )

def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def uses_sqlite() -> bool:
    return database_url().startswith("sqlite")
