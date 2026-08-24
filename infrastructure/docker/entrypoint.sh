#!/bin/sh
# Role dispatcher. One image, four roles — see docs/adr/0015-single-image.md
#
# POSIX sh, not bash: the runtime image is Alpine and deliberately carries no
# bash. Nothing below needs it, so the dispatcher must not reintroduce a package
# into an image whose release gate rejects every HIGH or CRITICAL CVE.
#
# `exec` in every branch so the process replaces the shell: without it PID 1 is
# the shell, SIGTERM never reaches the application, and every container stop
# becomes a ten-second wait followed by SIGKILL — for a worker, dropped jobs.

# No `pipefail`: it is a bash/busybox extension that dash rejects outright, and
# there is not a single pipe below for it to guard.
set -eu

ROLE="${1:-api}"
# Not `shift || true`: in dash a shift past the last argument is a special
# builtin error that aborts the script outright, and `|| true` does not catch
# it. Unreachable through `CMD ["api"]`, but not worth leaving as a trap.
if [ "$#" -gt 0 ]; then shift; fi

case "$ROLE" in
  api)
    # The graceful-shutdown timeout is not a nicety, it is the upper bound.
    #
    # uvicorn waits for open connections *before* running the lifespan shutdown,
    # and `/api/events` is a server-sent event stream that lives as long as the
    # browser wants it. Without a bound, one idle tab meant SIGTERM waited
    # forever and Docker's ten-second stop timeout ended it with SIGKILL —
    # dropping whatever else was in flight instead of draining it. Eight seconds
    # leaves room to close inside that window.
    #
    # The application also ends its own streams on SIGTERM, so this should never
    # be reached. It is here because "should never" is not a guarantee.
    exec uvicorn pornarr_api.main:create_app \
      --factory \
      --host 0.0.0.0 \
      --port 8000 \
      --timeout-graceful-shutdown 8 \
      "$@"
    ;;

  worker)
    WORKER_SETTINGS="pornarr_worker.settings.WorkerSettings"
    case "${1:-}" in
      default)
        shift
        ;;
      import)
        WORKER_SETTINGS="pornarr_worker.settings.ImportWorkerSettings"
        shift
        ;;
      transcode)
        WORKER_SETTINGS="pornarr_worker.settings.TranscodeWorkerSettings"
        shift
        ;;
      indexer)
        WORKER_SETTINGS="pornarr_worker.settings.IndexerWorkerSettings"
        shift
        ;;
    esac
    exec arq "$WORKER_SETTINGS" "$@"
    ;;

  beat)
    exec arq pornarr_worker.settings.SchedulerSettings "$@"
    ;;

  migrate)
    exec alembic upgrade head
    ;;

  shell)
    exec /bin/sh "$@"
    ;;

  *)
    # An unknown role is almost always a typo in the compose file. Running it as
    # a command silently would start something nobody intended.
    echo "unknown role: ${ROLE}" >&2
    echo "expected one of: api, worker, beat, migrate, shell" >&2
    exit 64
    ;;
esac
