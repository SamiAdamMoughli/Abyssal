"""Celery application factory + beat schedule.

Beat tasks:
  fetch_and_score_vessels  — every 5 minutes (real-time vessel tracking)
  sync_iuu_list            — daily at 02:00 UTC (CCAMLR/RFMO/TMT list refresh)
  sync_sanctions           — daily at 03:00 UTC (OpenSanctions bulk refresh)
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from spyhop.config import get_settings

settings = get_settings()

celery_app = Celery(
    "spyhop",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["spyhop.worker.tasks"],
)

celery_app.conf.update(
    # --- Serialization -------------------------------------------------------
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,

    # --- Queues --------------------------------------------------------------
    task_default_queue="default",
    task_routes={
        "spyhop.worker.tasks.fetch_and_score_vessels": {"queue": "scoring"},
        "spyhop.worker.tasks.sync_iuu_list": {"queue": "sync"},
        "spyhop.worker.tasks.sync_sanctions": {"queue": "sync"},
    },

    # --- Resilience ----------------------------------------------------------
    task_acks_late=True,           # only ack after task completes (safe re-queue)
    task_reject_on_worker_lost=True,
    task_track_started=True,

    # --- Performance ---------------------------------------------------------
    worker_prefetch_multiplier=1,  # one task at a time per worker slot
    task_compression="gzip",

    # --- Result expiry -------------------------------------------------------
    result_expires=3600,           # keep results 1h in Redis

    # --- Beat schedule -------------------------------------------------------
    beat_schedule={
        "fetch-and-score-vessels-every-5min": {
            "task": "spyhop.worker.tasks.fetch_and_score_vessels",
            "schedule": 300.0,  # 5 minutes
            "options": {"queue": "scoring"},
        },
        "sync-iuu-list-daily": {
            "task": "spyhop.worker.tasks.sync_iuu_list",
            "schedule": crontab(hour=2, minute=0),
            "options": {"queue": "sync"},
        },
        "sync-sanctions-daily": {
            "task": "spyhop.worker.tasks.sync_sanctions",
            "schedule": crontab(hour=3, minute=0),
            "options": {"queue": "sync"},
        },
    },
)
