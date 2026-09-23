from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import database_url, ensure_dirs

ensure_dirs()

_DB_URL = database_url()
_IS_SQLITE = _DB_URL.startswith("sqlite")
_CONNECT_ARGS = {"check_same_thread": False} if _IS_SQLITE else {}
_engine_kwargs: dict = {"connect_args": _CONNECT_ARGS, "pool_pre_ping": True}
if not _IS_SQLITE:
    _engine_kwargs.update({"pool_size": 5, "max_overflow": 10, "pool_recycle": 1800})

engine = create_engine(_DB_URL, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def uses_sqlite() -> bool:
    return database_url().startswith("sqlite")
