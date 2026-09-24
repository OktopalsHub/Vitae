# Phase 20: Worker Reliability

## Goal

Make background jobs safe for long-running production work and worker restarts.

## Changes

- Worker leases are now kept alive with periodic heartbeats.
- Stale-job recovery uses the latest heartbeat instead of only the original lock time.
- Heartbeats are tied to the current lock owner so an old worker cannot extend a lease after another worker has reclaimed it.
- Worker logging uses the application's structured logging configuration.
- Worker shutdown cancels heartbeat tasks cleanly.
- Added regression tests for heartbeat updates, stale-job recovery, and lock ownership.

## Failure and recovery model

1. A worker claims a queued job and receives a unique lock owner.
2. The worker writes an initial heartbeat.
3. Long-running work refreshes the heartbeat periodically.
4. If the worker dies, the heartbeat stops.
5. After the lease expires, another worker can reclaim the job.
6. The reclaimed job gets another attempt and a new lock owner.
7. Existing retry and dead-letter behavior still applies when max_attempts is reached.

The worker does not automatically mark a job complete during shutdown. This avoids declaring an external operation successful when the process may have stopped before the operation finished.

## Production checklist

- Run separate worker processes for each queue.
- Keep DEFAULT_LEASE_SECONDS longer than the normal heartbeat interval.
- Keep jobs idempotent because a crashed worker can be reclaimed and retried.
- Monitor dead jobs and last_error.
- Use a shared production database for all worker replicas.

## Acceptance criteria

- Long-running jobs refresh their lease.
- A dead worker can be recovered after the heartbeat lease expires.
- A previous worker cannot heartbeat a job after another worker owns it.
- Existing retry/dead-letter behavior remains unchanged.
- Worker logs use the same structured logging setup as the web process.
