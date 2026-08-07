#!/usr/bin/env bash
# Role dispatcher. One image, four roles — see docs/adr/0015-single-image.md
#
# `exec` in every branch so the process replaces the shell: without it PID 1 is
# bash, SIGTERM never reaches the application, and every container stop becomes
# a ten-second wait followed by SIGKILL — which for a worker means dropped jobs.

set -euo pipefail

ROLE="${1:-api}"
shift || true

case "$ROLE" in
  api)
    exec uvicorn pornarr_api.main:create_app \
      --factory \
      --host 0.0.0.0 \
      --port 8000 \
      "$@"
    ;;

  worker)
    exec arq pornarr_worker.settings.WorkerSettings "$@"
    ;;

  beat)
    exec arq pornarr_worker.settings.SchedulerSettings "$@"
    ;;

  migrate)
    exec alembic upgrade head
    ;;

  shell)
    exec /bin/bash "$@"
    ;;

  *)
    # An unknown role is almost always a typo in the compose file. Running it as
    # a command silently would start something nobody intended.
    echo "unknown role: ${ROLE}" >&2
    echo "expected one of: api, worker, beat, migrate, shell" >&2
    exit 64
    ;;
esac
