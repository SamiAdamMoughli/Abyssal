"""Population Stability Index (PSI) drift monitor.

PSI measures how much the current score distribution has shifted relative to
the training-time baseline.

  PSI < 0.10  — stable          (no action)
  0.10–0.20   — slight drift    (warning; schedule early retrain)
  PSI >= 0.20 — significant     (alert; retrain immediately)

The baseline histogram (10 equal-width bins over [0, 1]) is stored in
MLModelRegistry.metrics_json["score_histogram"] at training time.
The current distribution is computed from the last 24 h of MLPredictionLog.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

import ujson
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from spyhop.db.models import MLModelRegistry, MLPredictionLog

N_BINS = 10
BIN_EDGES = [i / N_BINS for i in range(N_BINS + 1)]  # 0.0, 0.1, … 1.0
EPSILON = 1e-6  # prevent log(0)
LOOKBACK_HOURS = 24


def _histogram(scores: list[float]) -> list[float]:
    """Normalised frequency histogram over BIN_EDGES."""
    counts = [0] * N_BINS
    for s in scores:
        idx = min(int(s * N_BINS), N_BINS - 1)
        counts[idx] += 1
    total = max(len(scores), 1)
    return [c / total for c in counts]


def compute_psi(
    baseline: list[float],
    current: list[float],
) -> float:
    """PSI = Σ (actual - expected) * ln(actual / expected)."""
    if len(baseline) != len(current):
        raise ValueError("baseline and current must have the same length")
    psi = 0.0
    for exp, act in zip(baseline, current):
        exp = max(exp, EPSILON)
        act = max(act, EPSILON)
        psi += (act - exp) * math.log(act / exp)
    return psi


def _status(psi: float, warn: float, critical: float) -> str:
    if psi >= critical:
        return "critical"
    if psi >= warn:
        return "warning"
    return "stable"


def check_drift(
    model_name: str,
    session: Session,
    redis_client: Any,
    psi_warn: float = 0.10,
    psi_critical: float = 0.20,
) -> dict[str, Any]:
    """Compute PSI for the active model and cache result in Redis.

    Returns dict with keys: psi, status, n_current, model_version.
    Writes result to Redis key ml:drift:{model_name} with 7200s TTL.
    """
    # Pull active model baseline histogram
    active = session.execute(
        select(MLModelRegistry).where(
            MLModelRegistry.model_name == model_name,
            MLModelRegistry.status == "active",
        )
    ).scalar_one_or_none()

    if active is None:
        return {"psi": None, "status": "no_active_model", "n_current": 0}

    metrics = active.metrics_json or {}
    baseline_hist = metrics.get("score_histogram")
    if not baseline_hist or len(baseline_hist) != N_BINS:
        return {
            "psi": None,
            "status": "no_baseline",
            "n_current": 0,
            "model_version": active.version,
        }

    # Pull recent predictions
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    rows = session.execute(
        select(MLPredictionLog.predicted_score).where(
            MLPredictionLog.model_name == model_name,
            MLPredictionLog.created_at >= cutoff,
        )
    ).scalars().all()

    current_scores = [float(r) for r in rows]
    n_current = len(current_scores)

    if n_current < 10:
        return {
            "psi": None,
            "status": "insufficient_data",
            "n_current": n_current,
            "model_version": active.version,
        }

    current_hist = _histogram(current_scores)
    psi = compute_psi(baseline_hist, current_hist)
    status = _status(psi, psi_warn, psi_critical)

    result: dict[str, Any] = {
        "psi": round(psi, 4),
        "status": status,
        "n_current": n_current,
        "model_version": active.version,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    redis_client.setex(
        f"ml:drift:{model_name}",
        7200,
        ujson.dumps(result),
    )
    return result


def scores_to_histogram(scores: list[float]) -> list[float]:
    """Convenience: build normalised histogram from a list of scores."""
    return _histogram(scores)
