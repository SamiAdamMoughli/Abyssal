"""Celery tasks for the MLOps pipeline.

Four tasks, all routed to the 'ml' queue so they never compete with
the real-time scoring queue or the reference-data sync queue:

  train_risk_model       — Sunday 04:00 UTC. Extracts features from
                           vessel_positions, fits GBR, registers as shadow.

  train_behavior_model   — Sunday 04:30 UTC. Fits RandomForest on
                           vessel_tracks kinematic features, registers shadow.

  promote_shadow_model   — Monday 05:00 UTC. Compares shadow vs active metrics
                           for each model; promotes if shadow is better OR if
                           there is no active model yet (cold start).

  monitor_score_drift    — Every 6 h at :45. Computes PSI on the last 24 h of
                           ml_prediction_log vs training baseline. Logs to
                           Redis and emits a structured warning when PSI > 0.10.

  prune_prediction_log   — Daily 01:30 UTC. Deletes ml_prediction_log rows
                           older than 30 days to keep the table lean.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import redis as sync_redis_lib
from celery.utils.log import get_task_logger
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from spyhop.config import get_settings
from spyhop.worker.celery_app import celery_app

log = get_task_logger(__name__)
settings = get_settings()

_sync_engine = create_engine(
    settings.SYNC_DATABASE_URL,
    pool_size=3,
    max_overflow=5,
    pool_pre_ping=True,
    pool_recycle=1800,
)
MLSession = sessionmaker(bind=_sync_engine, autocommit=False, autoflush=False)

_redis = sync_redis_lib.Redis.from_url(
    settings.REDIS_URL,
    decode_responses=True,
    socket_timeout=settings.REDIS_SOCKET_TIMEOUT,
)

REDIS_VERSION_KEY = "ml:model_version:{model_name}"


def _publish_promotion(model_name: str, version: str) -> None:
    """Write the active version to Redis so loaders detect the swap."""
    key = REDIS_VERSION_KEY.format(model_name=model_name)
    _redis.set(key, version)
    log.info(
        "ml.promoted model=%s version=%s", model_name, version
    )


# ---------------------------------------------------------------------------
# Task: train_risk_model
# ---------------------------------------------------------------------------

@celery_app.task(
    name="spyhop.worker.ml_tasks.train_risk_model",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
    soft_time_limit=1800,
    time_limit=2100,
)
def train_risk_model(self: Any) -> dict[str, Any]:
    """Extract vessel_positions data, train GBR, register as shadow."""
    from spyhop.ml.training.risk import train

    try:
        with MLSession() as session:
            result = train(session, settings)
        log.info("train_risk_model result=%s", result)
        return result
    except Exception as exc:
        log.exception("train_risk_model failed: %s", exc)
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# Task: train_behavior_model
# ---------------------------------------------------------------------------

@celery_app.task(
    name="spyhop.worker.ml_tasks.train_behavior_model",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
    soft_time_limit=900,
    time_limit=1200,
)
def train_behavior_model(self: Any) -> dict[str, Any]:
    """Extract vessel_tracks kinematic features, train RF classifier."""
    from spyhop.ml.training.behavior import train

    try:
        with MLSession() as session:
            result = train(session, settings)
        log.info("train_behavior_model result=%s", result)
        return result
    except Exception as exc:
        log.exception("train_behavior_model failed: %s", exc)
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# Task: promote_shadow_model
# ---------------------------------------------------------------------------

@celery_app.task(
    name="spyhop.worker.ml_tasks.promote_shadow_model",
    bind=True,
    max_retries=1,
    soft_time_limit=120,
    time_limit=180,
)
def promote_shadow_model(self: Any) -> dict[str, Any]:
    """Compare shadow vs active for each model; promote if shadow is better.

    Promotion criteria for risk_scorer: shadow R² > active R²
    (or no active model yet — cold-start automatic promotion).
    For behavior_classifier: shadow f1_weighted > active f1_weighted.
    """
    from spyhop.ml import registry as reg

    results: dict[str, Any] = {}
    candidates = [
        ("risk_scorer", "r2", settings.ML_PROMOTE_MIN_R2),
        ("behavior_classifier", "f1_weighted", 0.50),
    ]

    try:
        with MLSession() as session:
            for model_name, metric_key, min_threshold in candidates:
                shadow = reg.get_shadow(model_name, session)
                active = reg.get_active(model_name, session)

                if shadow is None:
                    results[model_name] = "no_shadow"
                    continue

                shadow_metric = (shadow.metrics_json or {}).get(
                    metric_key, -1.0
                )
                active_metric = (active.metrics_json or {}).get(
                    metric_key, -1.0
                ) if active else -1.0

                if shadow_metric < min_threshold:
                    log.warning(
                        "ml.promote_rejected model=%s shadow_%s=%.4f "
                        "min=%.4f",
                        model_name, metric_key,
                        shadow_metric, min_threshold,
                    )
                    reg.retire(shadow.id, session)
                    results[model_name] = "rejected"
                    continue

                if active is None or shadow_metric > active_metric:
                    reg.promote(shadow.id, session)
                    session.commit()
                    _publish_promotion(model_name, shadow.version)
                    log.info(
                        "ml.promoted model=%s version=%s %s=%.4f",
                        model_name, shadow.version,
                        metric_key, shadow_metric,
                    )
                    results[model_name] = {
                        "promoted": shadow.version,
                        metric_key: shadow_metric,
                    }
                else:
                    log.info(
                        "ml.shadow_not_better model=%s shadow=%.4f "
                        "active=%.4f",
                        model_name, shadow_metric, active_metric,
                    )
                    results[model_name] = "not_better"

        return results
    except Exception as exc:
        log.exception("promote_shadow_model failed: %s", exc)
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# Task: monitor_score_drift
# ---------------------------------------------------------------------------

@celery_app.task(
    name="spyhop.worker.ml_tasks.monitor_score_drift",
    soft_time_limit=120,
    time_limit=180,
)
def monitor_score_drift() -> dict[str, Any]:
    """Compute PSI for the active risk_scorer; log warning if drifting."""
    from spyhop.ml.drift import check_drift

    try:
        with MLSession() as session:
            result = check_drift(
                model_name="risk_scorer",
                session=session,
                redis_client=_redis,
                psi_warn=settings.ML_DRIFT_PSI_WARN,
                psi_critical=settings.ML_DRIFT_PSI_CRITICAL,
            )

        status = result.get("status", "unknown")
        if status == "critical":
            log.error(
                "ml.drift_critical model=risk_scorer psi=%.4f — "
                "schedule immediate retrain",
                result.get("psi", -1),
            )
        elif status == "warning":
            log.warning(
                "ml.drift_warning model=risk_scorer psi=%.4f — "
                "schedule early retrain",
                result.get("psi", -1),
            )
        else:
            log.info(
                "ml.drift_stable model=risk_scorer psi=%s status=%s",
                result.get("psi"), status,
            )
        return result
    except Exception as exc:
        log.exception("monitor_score_drift failed: %s", exc)
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Task: prune_prediction_log
# ---------------------------------------------------------------------------

@celery_app.task(
    name="spyhop.worker.ml_tasks.prune_prediction_log",
    soft_time_limit=120,
    time_limit=180,
)
def prune_prediction_log() -> dict[str, Any]:
    """Delete ml_prediction_log rows older than 30 days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    try:
        with MLSession() as session:
            result = session.execute(
                text(
                    "DELETE FROM ml_prediction_log WHERE created_at < :cutoff"
                ),
                {"cutoff": cutoff},
            )
            session.commit()
            deleted = result.rowcount
        log.info("ml.prune_prediction_log deleted=%d", deleted)
        return {"status": "ok", "deleted": deleted}
    except Exception as exc:
        log.exception("prune_prediction_log failed: %s", exc)
        return {"status": "error", "error": str(exc)}
