"""Risk scorer training pipeline.

Trains a GradientBoostingRegressor on the full vessel_positions table,
using the current heuristic risk_score as the bootstrap training label.
As analysts confirm or override alerts, those corrections become the ground
truth for subsequent training runs.

Training sequence:
  1. Query vessel_positions (all rows with risk_score > 0).
  2. Build feature matrix X (23 features) and target y (risk_score 0–1).
  3. Train/eval split (80/20 chronological — avoids leaking future state).
  4. Fit GradientBoostingRegressor.
  5. Evaluate: MAE, R², score histogram for drift baseline.
  6. Serialize with joblib to artifact_path.
  7. Register in MLModelRegistry as status='shadow'.

Returns a metrics dict.  The caller (Celery task) decides whether to
promote immediately (cold start: no active model) or leave as shadow
for the weekly promotion check.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

log = logging.getLogger(__name__)

MODEL_NAME = "risk_scorer"
TEST_SPLIT = 0.20


def train(session: "Session", settings: "Settings") -> dict:  # type: ignore[name-defined]
    """Extract data, fit model, register as shadow.  Returns metrics dict."""
    import joblib
    import numpy as np
    from sklearn.ensemble import GradientBoostingRegressor
    from sklearn.metrics import mean_absolute_error, r2_score
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from spyhop.db.models import MLModelRegistry, VesselPosition
    from spyhop.ml.drift import scores_to_histogram
    from spyhop.ml.features import RISK_FEATURE_NAMES, extract_from_row
    from spyhop.ml import registry as reg

    # ------------------------------------------------------------------
    # 1. Load training data
    # ------------------------------------------------------------------
    rows = session.execute(
        select(VesselPosition).where(VesselPosition.risk_score > 0)
    ).scalars().all()

    if len(rows) < settings.ML_MIN_TRAIN_SAMPLES:
        log.warning(
            "risk_scorer.train skipped: only %d rows (min %d)",
            len(rows), settings.ML_MIN_TRAIN_SAMPLES,
        )
        return {
            "status": "skipped",
            "reason": "insufficient_data",
            "n": len(rows),
        }

    X = np.array([extract_from_row(r) for r in rows], dtype=np.float32)
    y = np.array([r.risk_score for r in rows], dtype=np.float32)

    # ------------------------------------------------------------------
    # 2. Chronological split (rows already ordered by update time in DB)
    # ------------------------------------------------------------------
    split_idx = max(1, int(len(rows) * (1 - TEST_SPLIT)))
    X_train, X_test = X[:split_idx], X[split_idx:]
    y_train, y_test = y[:split_idx], y[split_idx:]

    # ------------------------------------------------------------------
    # 3. Fit
    # ------------------------------------------------------------------
    model = GradientBoostingRegressor(
        n_estimators=200,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        min_samples_leaf=5,
        random_state=42,
    )
    model.fit(X_train, y_train)

    # ------------------------------------------------------------------
    # 4. Evaluate
    # ------------------------------------------------------------------
    y_pred = model.predict(X_test).clip(0, 1)
    mae = float(mean_absolute_error(y_test, y_pred))
    r2 = float(r2_score(y_test, y_pred)) if len(y_test) > 1 else 0.0
    score_histogram = scores_to_histogram(y_train.tolist())

    log.info(
        "risk_scorer.trained n_train=%d n_test=%d mae=%.4f r2=%.4f",
        len(X_train), len(X_test), mae, r2,
    )

    # ------------------------------------------------------------------
    # 5. Serialize artifact
    # ------------------------------------------------------------------
    version = datetime.now(timezone.utc).strftime("%Y%m%d.%H%M")
    artifact_path = os.path.join(
        settings.MODEL_ARTIFACTS_PATH,
        MODEL_NAME,
        version,
        "model.joblib",
    )
    reg.ensure_artifact_dir(artifact_path)
    joblib.dump(
        {"model": model, "feature_names": RISK_FEATURE_NAMES},
        artifact_path,
    )
    log.info("risk_scorer.serialized path=%s", artifact_path)

    # ------------------------------------------------------------------
    # 6. Register in DB
    # ------------------------------------------------------------------
    metrics = {
        "mae": round(mae, 6),
        "r2": round(r2, 6),
        "n_train": len(X_train),
        "n_test": len(X_test),
        "score_histogram": [round(v, 6) for v in score_histogram],
    }
    reg.register(
        model_name=MODEL_NAME,
        version=version,
        artifact_path=artifact_path,
        metrics=metrics,
        feature_names=RISK_FEATURE_NAMES,
        session=session,
    )
    session.commit()

    return {"status": "ok", "version": version, **metrics}
