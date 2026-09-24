"""Durable scheduler: enqueue work instead of executing heavy jobs in the web process."""

from __future__ import annotations

import asyncio
import logging
import os

from app.db import SessionLocal
from app.models import User
from app.workers.queue import enqueue_job

logger = logging.getLogger(__name__)

CATALOGUE_SYNC_INTERVAL_SECONDS = int(os.getenv("CATALOGUE_SYNC_INTERVAL_SECONDS", "3600"))
_CATALOGUE_STARTUP_DELAY_SECONDS = int(os.getenv("CATALOGUE_SYNC_STARTUP_DELAY_SECONDS", "2"))
USER_RANK_INTERVAL_SECONDS = int(os.getenv("USER_RANK_INTERVAL_SECONDS", str(3 * 3600)))
_USER_RANK_STARTUP_DELAY_SECONDS = int(os.getenv("USER_RANK_STARTUP_DELAY_SECONDS", "120"))


def _env_disabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes"}


def enqueue_catalogue_sync() -> int:
    with SessionLocal() as db:
        job = enqueue_job(
            db,
            kind="catalogue_sync",
            queue="catalogue",
            idempotency_key="catalogue-sync:singleton",
        )
        db.commit()
        return job.id


def enqueue_user_rank_refresh() -> int:
    count = 0
    with SessionLocal() as db:
        users = db.query(User.id).filter(User.is_active.is_(True)).all()
        for (uid,) in users:
            enqueue_job(
                db,
                kind="user_rescore",
                queue="matching",
                payload={"user_id": str(uid)},
                idempotency_key=f"user-rescore:{uid}",
            )
            count += 1
        db.commit()
    return count


async def catalogue_sync_loop() -> None:
    await asyncio.sleep(_CATALOGUE_STARTUP_DELAY_SECONDS)
    while True:
        try:
            job_id = enqueue_catalogue_sync()
            logger.info("queued catalogue sync job=%s", job_id)
        except Exception:
            logger.exception("failed to queue catalogue sync")
        await asyncio.sleep(CATALOGUE_SYNC_INTERVAL_SECONDS)


async def user_rank_refresh_loop() -> None:
    await asyncio.sleep(_USER_RANK_STARTUP_DELAY_SECONDS)
    while True:
        try:
            count = enqueue_user_rank_refresh()
            logger.info("queued matching refresh jobs=%s", count)
        except Exception:
            logger.exception("failed to queue matching refresh")
        await asyncio.sleep(USER_RANK_INTERVAL_SECONDS)


def start_catalogue_sync_task() -> list[asyncio.Task]:
    tasks: list[asyncio.Task] = []
    if not _env_disabled("CATALOGUE_SYNC_DISABLED"):
        tasks.append(asyncio.create_task(catalogue_sync_loop(), name="catalogue-scheduler"))
    if not _env_disabled("USER_RANK_REFRESH_DISABLED"):
        tasks.append(asyncio.create_task(user_rank_refresh_loop(), name="matching-scheduler"))
    return tasks
