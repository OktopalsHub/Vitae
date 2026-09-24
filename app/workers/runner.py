from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal

from app.db import SessionLocal
from app.workers.queue import claim_next_job, complete_job, fail_job
from app.workers.tasks import execute_job

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("vitae.worker")


async def run_worker(queue: str = "default", poll_seconds: float = 2.0) -> None:
    stopping = False

    def stop(*_args):
        nonlocal stopping
        stopping = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, stop)
        except ValueError:
            pass

    while not stopping:
        with SessionLocal() as db:
            job = claim_next_job(db, queue=queue)
            if job is None:
                db.rollback()
            else:
                db.commit()
        if job is None:
            await asyncio.sleep(poll_seconds)
            continue

        try:
            payload = json.loads(job.payload_json or "{}")
            await execute_job(job.kind, payload)
        except Exception as exc:
            with SessionLocal() as db:
                current = db.get(type(job), job.id)
                if current:
                    fail_job(db, current, exc)
                db.commit()
            log.exception("worker job failed id=%s kind=%s", job.id, job.kind)
        else:
            with SessionLocal() as db:
                current = db.get(type(job), job.id)
                if current:
                    complete_job(db, current)
                db.commit()
            log.info("worker job completed id=%s kind=%s", job.id, job.kind)


def main() -> None:
    parser = argparse.ArgumentParser(description="Vitae durable worker")
    parser.add_argument("--queue", default="default")
    parser.add_argument("--poll", type=float, default=2.0)
    args = parser.parse_args()
    asyncio.run(run_worker(args.queue, args.poll))


if __name__ == "__main__":
    main()
