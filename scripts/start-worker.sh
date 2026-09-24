#!/bin/sh
set -eu

exec vitae-worker \
  --queue "${WORKER_QUEUE:-default}" \
  --poll "${WORKER_POLL_SECONDS:-2}"
