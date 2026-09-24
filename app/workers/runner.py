from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal

from app.db import SessionLocal
from app.models import WorkerJob, WorkerJobStatus
from app.observability import configure_logging
from app.worker_metrics import record
from app.workers.queue import DEFAULT_LEASE_SECONDS, claim_next_job, complete_job, fail_job, heartbeat_job
from app.workers.tasks import execute_job

configure_logging()
log = logging.getLogger("vitae.worker")
HEARTBEAT_INTERVAL_SECONDS = max(10, DEFAULT_LEASE_SECONDS // 3)


async def _heartbeat_loop(job_id: int, lock_owner: str, interval_seconds: int) -> None:
    """Keep a long-running job lease alive until the task finishes."""
    while True:
        await asyncio.sleep(interval_seconds)
        with SessionLocal() as db:
            current = db.get(WorkerJob, job_id)
            if current is None or current.status != WorkerJobStatus.RUNNING.value:
                return
            if not heartbeat_job(db, current, lock_owner=lock_owner):
                return
            db.commit()


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

        started = asyncio.get_running_loop().time()
        heartbeat_task = asyncio.create_task(
            _heartbeat_loop(job.id, job.lock_owner, HEARTBEAT_INTERVAL_SECONDS),
            name=f"worker-heartbeat-{job.id}",
        )
        try:
            payload = json.loads(job.payload_json or "{}")
            await execute_job(job.kind, payload)
        except Exception as exc:
            with SessionLocal() as db:
                current = db.get(WorkerJob, job.id)
                if current:
                    fail_job(db, current, exc)
                db.commit()
            duration_ms = (asyncio.get_running_loop().time() - started) * 1000
            record(job.kind, "failed", duration_ms)
            log.exception(
                "worker job failed id=%s kind=%s duration_ms=%.2f",
                job.id,
                job.kind,
                duration_ms,
            )
        else:
            with SessionLocal() as db:
                current = db.get(WorkerJob, job.id)
                if current:
                    complete_job(db, current)
                db.commit()
            duration_ms = (asyncio.get_running_loop().time() - started) * 1000
            record(job.kind, "completed", duration_ms)
            log.info(
                "worker job completed id=%s kind=%s duration_ms=%.2f",
                job.id,
                job.kind,
                duration_ms,
            )
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Vitae durable worker")
    parser.add_argument("--queue", default="default")
    parser.add_argument("--poll", type=float, default=2.0)
    args = parser.parse_args()
    asyncio.run(run_worker(args.queue, args.poll))


if __name__ == "__main__":
    main()
