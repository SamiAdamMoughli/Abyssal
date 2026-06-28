"""Celery application factory + beat schedule.

Beat tasks:
  fetch_and_score_vessels        — every 5 minutes (real-time vessel tracking)
  sync_iuu_list                  — daily at 02:00 UTC
  sync_sanctions                 — daily at 03:00 UTC
  sync_environment_raster        — hourly at :05 UTC
  brain.evaluate_spatialized_batch — every 30 s (rule evaluation)
  train_risk_model               — Sunday 04:00 UTC (ML retraining)
  train_behavior_model           — Sunday 04:30 UTC (ML retraining)
  promote_shadow_model           — Monday 05:00 UTC (auto-promote if better)
  monitor_score_drift            — every 6 h at :45 (PSI drift check)
  prune_prediction_log           — daily 01:30 UTC (log housekeeping)
  snapshot_vessel_positions      — hourly at :50 UTC (corridor accumulation)
  materialize_h3_corridors       — Sunday 01:00 UTC (weekly corridor rollup)
  prune_vessel_snapshots         — daily 00:30 UTC (90-day retention)
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
    include=[
        "spyhop.worker.tasks",
        "spyhop.worker.ml_tasks",
        "vesselx.brain.tasks",
    ],
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
        "spyhop.worker.tasks.sync_environment_raster": {"queue": "sync"},
        "spyhop.worker.tasks.compute_h3_context": {"queue": "sync"},
        "spyhop.worker.tasks.prune_vessel_tracks": {"queue": "sync"},
        "spyhop.worker.tasks.snapshot_vessel_positions": {"queue": "sync"},
        "spyhop.worker.tasks.materialize_h3_corridors": {"queue": "sync"},
        "spyhop.worker.tasks.prune_vessel_snapshots": {"queue": "sync"},
        # Brain evaluation — isolated queue to prevent starvation
        "brain.evaluate_spatialized_batch": {"queue": "brain"},
        "brain.evaluate_vessel_by_mmsi": {"queue": "brain"},
        # ML pipeline — isolated so long training never delays live tasks
        "spyhop.worker.ml_tasks.train_risk_model": {"queue": "ml"},
        "spyhop.worker.ml_tasks.train_behavior_model": {"queue": "ml"},
        "spyhop.worker.ml_tasks.promote_shadow_model": {"queue": "ml"},
        "spyhop.worker.ml_tasks.monitor_score_drift": {"queue": "ml"},
        "spyhop.worker.ml_tasks.prune_prediction_log": {"queue": "ml"},
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
        "sync-environment-raster-hourly": {
            "task": "spyhop.worker.tasks.sync_environment_raster",
            "schedule": crontab(minute=5),   # :05 past every hour
            "options": {"queue": "sync"},
        },
        "compute-h3-context-every-6h": {
            "task": "spyhop.worker.tasks.compute_h3_context",
            "schedule": crontab(minute=30, hour="*/6"),
            "options": {"queue": "sync"},
        },
        "brain-evaluate-spatialized-batch-every-30s": {
            "task": "brain.evaluate_spatialized_batch",
            "schedule": 30.0,
            # soft_time_limit/time_limit are enforced by the worker process;
            # keeping them just under the beat interval prevents pile-up when
            # a batch stalls (e.g. Redis timeout, slow IUU lookup).
            "options": {
                "queue": "brain",
                "soft_time_limit": 25,
                "time_limit": 28,
            },
        },
        "prune-vessel-tracks-weekly": {
            "task": "spyhop.worker.tasks.prune_vessel_tracks",
            "schedule": crontab(hour=4, minute=0, day_of_week=0),
            "options": {"queue": "sync"},
        },
        # --- ML pipeline schedule --------------------------------------------
        "ml-train-risk-scorer-weekly": {
            "task": "spyhop.worker.ml_tasks.train_risk_model",
            "schedule": crontab(hour=4, minute=0, day_of_week=0),
            "options": {"queue": "ml"},
        },
        "ml-train-behavior-classifier-weekly": {
            "task": "spyhop.worker.ml_tasks.train_behavior_model",
            "schedule": crontab(hour=4, minute=30, day_of_week=0),
            "options": {"queue": "ml"},
        },
        "ml-promote-shadow-model-weekly": {
            "task": "spyhop.worker.ml_tasks.promote_shadow_model",
            "schedule": crontab(hour=5, minute=0, day_of_week=1),
            "options": {"queue": "ml"},
        },
        "ml-monitor-score-drift-every-6h": {
            "task": "spyhop.worker.ml_tasks.monitor_score_drift",
            "schedule": crontab(minute=45, hour="*/6"),
            "options": {"queue": "ml"},
        },
        "ml-prune-prediction-log-daily": {
            "task": "spyhop.worker.ml_tasks.prune_prediction_log",
            "schedule": crontab(hour=1, minute=30),
            "options": {"queue": "ml"},
        },
        # --- Corridor analysis pipeline --------------------------------------
        "corridor-snapshot-hourly": {
            "task": "spyhop.worker.tasks.snapshot_vessel_positions",
            "schedule": crontab(minute=50),   # :50 past every hour
            "options": {"queue": "sync"},
        },
        "corridor-materialize-weekly": {
            "task": "spyhop.worker.tasks.materialize_h3_corridors",
            "schedule": crontab(hour=1, minute=0, day_of_week=0),
            "options": {"queue": "sync"},
        },
        "corridor-prune-daily": {
            "task": "spyhop.worker.tasks.prune_vessel_snapshots",
            "schedule": crontab(hour=0, minute=30),
            "options": {"queue": "sync"},
        },
        # --- Ecological mask refresh ----------------------------------------
        "ecological-masks-nightly": {
            "task": "spyhop.worker.tasks.refresh_ecological_masks",
            "schedule": crontab(hour=0, minute=15),
            "options": {"queue": "sync"},
        },
    },
)
