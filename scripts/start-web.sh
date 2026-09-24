#!/bin/sh
set -eu

exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8765}" \
  --workers "${WEB_WORKERS:-1}" \
  --proxy-headers \
  --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-*}" \
  --timeout-keep-alive "${KEEP_ALIVE_SECONDS:-5}"
