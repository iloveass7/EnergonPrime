#!/bin/sh
set -e
case "$1" in
  api)    exec uvicorn fuelops.api.main:app --host 0.0.0.0 --port 8080 --workers "${API_WORKERS:-2}" --no-access-log ;;
  worker) exec python -m fuelops.worker.main ;;
  *)      exec "$@" ;;
esac
