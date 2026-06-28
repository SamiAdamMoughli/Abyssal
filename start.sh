#!/bin/sh
# Entrypoint: dispatch to the right process based on SERVICE_TYPE env var.
# SERVICE_TYPE=api    → uvicorn (default)
# SERVICE_TYPE=worker → celery worker
# SERVICE_TYPE=beat   → celery beat
set -e

case "${SERVICE_TYPE:-api}" in
  worker)
    exec celery -A spyhop.worker.celery_app worker \
      --loglevel=info \
      --concurrency=4 \
      --queues=default,scoring,sync
    ;;
  beat)
    exec celery -A spyhop.worker.celery_app beat \
      --loglevel=info \
      --scheduler celery.beat.PersistentScheduler \
      --schedule /tmp/celerybeat-schedule
    ;;
  *)
    exec sh -c "uvicorn vesselx.spatial_engine.app:app \
      --host 0.0.0.0 \
      --port ${PORT:-8000} \
      --workers 4 \
      --loop uvloop \
      --http httptools \
      --log-level info"
    ;;
esac
