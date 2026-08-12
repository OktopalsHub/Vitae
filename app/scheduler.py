"""Background jobs: catalogue sync (1h) + user ranking refresh (3h)."""

from __future__ import annotations

import asyncio
import logging
import os

from app.db import SessionLocal
from app.models import User
from app.services import rescore_user_jobs, sync_public_jobs

logger = logging.getLogger(__name__)

# Public job catalogue — pull APIs, upsert, close missing.
CATALOGUE_SYNC_INTERVAL_SECONDS = int(
    os.getenv("CATALOGUE_SYNC_INTERVAL_SECONDS", "3600")
)  # 1 hour
_CATALOGUE_STARTUP_DELAY_SECONDS = int(
    os.getenv("CATALOGUE_SYNC_STARTUP_DELAY_SECONDS", "2")
)

# Personal ranking overlays — refresh existing user_jobs only (no fan-out).
USER_RANK_INTERVAL_SECONDS = int(
    os.getenv("USER_RANK_INTERVAL_SECONDS", str(3 * 3600))
)  # 3 hours
_USER_RANK_STARTUP_DELAY_SECONDS = int(
    os.getenv("USER_RANK_STARTUP_DELAY_SECONDS", "120")
)


def _env_disabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes"}


async def run_catalogue_sync_once() -> dict:
    db = SessionLocal()
    try:
        result = await sync_public_jobs(db)
        logger.info(
            "catalogue sync fetched=%s created=%s updated=%s reopened=%s closed=%s",
            result.get("fetched"),
            result.get("created"),
            result.get("updated"),
            result.get("reopened"),
            result.get("closed"),
        )
        return result
    finally:
        db.close()


def run_user_rank_refresh_once() -> dict[str, int]:
    """Rescore existing UserJob rows for every active user.

    Uses a short-lived session per user so that:
    - Lock contention is minimal (session closed before moving to next user).
    - One user's failure does not abort the entire sweep.
    """
    users_n = 0
    overlays_n = 0
    errors_n = 0

    # Fetch user IDs first in a separate short session, then process each user
    # with its own session to limit lock scope and crash blast radius.
    with SessionLocal() as id_db:
        user_ids: list = [
            uid for (uid,) in id_db.query(User.id).filter(User.is_active.is_(True)).all()
        ]

    for uid in user_ids:
        db = SessionLocal()
        try:
            user = db.get(User, uid)
            if user is None:
                continue
            n = rescore_user_jobs(db, user)
            db.commit()
            overlays_n += n
            users_n += 1
        except Exception:
            logger.exception("user rank refresh failed for user %s", uid)
            errors_n += 1
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            db.close()

    logger.info(
        "user rank refresh users=%s overlays=%s errors=%s",
        users_n,
        overlays_n,
        errors_n,
    )
    return {"users": users_n, "overlays": overlays_n, "errors": errors_n}


async def catalogue_sync_loop() -> None:
    """Every 1h: keep shared public job_listings fresh."""
    await asyncio.sleep(_CATALOGUE_STARTUP_DELAY_SECONDS)
    while True:
        try:
            await run_catalogue_sync_once()
        except Exception:
            logger.exception("hourly catalogue sync failed")
        await asyncio.sleep(CATALOGUE_SYNC_INTERVAL_SECONDS)


async def user_rank_refresh_loop() -> None:
    """Every 3h: refresh scores on existing user_jobs only."""
    await asyncio.sleep(_USER_RANK_STARTUP_DELAY_SECONDS)
    while True:
        try:
            await asyncio.to_thread(run_user_rank_refresh_once)
        except Exception:
            logger.exception("3h user rank refresh failed")
        await asyncio.sleep(USER_RANK_INTERVAL_SECONDS)


def start_catalogue_sync_task() -> list[asyncio.Task]:
    """Start background loops. Returns started tasks (may be empty if disabled)."""
    tasks: list[asyncio.Task] = []

    if _env_disabled("CATALOGUE_SYNC_DISABLED"):
        logger.info("catalogue sync loop disabled")
    else:
        logger.info(
            "catalogue sync: first run in %ss, then every %ss (1h default)",
            _CATALOGUE_STARTUP_DELAY_SECONDS,
            CATALOGUE_SYNC_INTERVAL_SECONDS,
        )
        tasks.append(asyncio.create_task(catalogue_sync_loop(), name="catalogue-sync"))

    if _env_disabled("USER_RANK_REFRESH_DISABLED"):
        logger.info("user rank refresh loop disabled")
    else:
        logger.info(
            "user rank refresh: first run in %ss, then every %ss (3h default)",
            _USER_RANK_STARTUP_DELAY_SECONDS,
            USER_RANK_INTERVAL_SECONDS,
        )
        tasks.append(
            asyncio.create_task(user_rank_refresh_loop(), name="user-rank-refresh")
        )

    return tasks
