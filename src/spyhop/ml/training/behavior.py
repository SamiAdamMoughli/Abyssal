"""Behavior classifier training pipeline.

Trains a RandomForestClassifier on kinematic features extracted from
vessel_tracks, using the existing heuristic behavior_status labels from
vessel_positions as bootstrap ground truth.

As analysts override or confirm behavior labels they feed forward into
subsequent retraining runs (once a correction table is wired up).

Feature vector: [sog_mean, sog_std, cog_turn_rate, tortuosity,
                 window_minutes, n_pings]  (BEHAVIOR_FEATURE_NAMES)
Target: behavior_status  ∈ {transit, trawling, loitering, anchored}
"""
from __future__ import annotations

import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

MODEL_NAME = "behavior_classifier"
WINDOW_HOURS = 4
MIN_PINGS = 3


def train(session: "Session", settings: "Settings") -> dict:  # type: ignore[name-defined]
    """Extract track data, fit classifier, register as shadow."""
    import joblib
    import numpy as np
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import accuracy_score, f1_score
    from sklearn.preprocessing import LabelEncoder
    from sqlalchemy import select

    from spyhop.analytics.motion_profile import MotionPing, profile_from_pings
    from spyhop.db.models import VesselPosition, VesselTrack
    from spyhop.ml.features import BEHAVIOR_CLASSES, BEHAVIOR_FEATURE_NAMES
    from spyhop.ml import registry as reg
    from geoalchemy2.shape import to_shape

    # ------------------------------------------------------------------
    # 1. Get labelled vessels (non-unknown behavior)
    # ------------------------------------------------------------------
    labelled = session.execute(
        select(VesselPosition.mmsi, VesselPosition.behavior_status).where(
            VesselPosition.behavior_status.in_(BEHAVIOR_CLASSES)
        )
    ).all()

    if not labelled:
        log.warning("behavior_classifier.train skipped: no labelled vessels")
        return {"status": "skipped", "reason": "no_labels"}

    mmsi_to_label = {row.mmsi: row.behavior_status for row in labelled}
    mmsi_list = list(mmsi_to_label.keys())

    # ------------------------------------------------------------------
    # 2. Load recent tracks for those vessels
    # ------------------------------------------------------------------
    cutoff = datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)
    track_rows = session.execute(
        select(VesselTrack).where(
            VesselTrack.mmsi.in_(mmsi_list),
            VesselTrack.timestamp >= cutoff,
        ).order_by(VesselTrack.mmsi, VesselTrack.timestamp)
    ).scalars().all()

    buckets: dict[str, list[MotionPing]] = defaultdict(list)
    for row in track_rows:
        pt = to_shape(row.position)
        buckets[row.mmsi].append(
            MotionPing(lat=pt.y, lon=pt.x, sog=row.sog, cog=row.cog,
                       ts=row.timestamp)
        )

    # ------------------------------------------------------------------
    # 3. Extract features + labels
    # ------------------------------------------------------------------
    X_rows: list[list[float]] = []
    y_labels: list[str] = []

    for mmsi, pings in buckets.items():
        if len(pings) < MIN_PINGS:
            continue
        label = mmsi_to_label.get(mmsi)
        if not label:
            continue
        profile = profile_from_pings(pings)
        if profile is None:
            continue
        f = profile.features
        X_rows.append([
            f.sog_mean,
            f.sog_std,
            f.cog_turn_rate,
            f.tortuosity,
            f.window_minutes,
            float(f.n_pings),
        ])
        y_labels.append(label)

    if len(X_rows) < settings.ML_MIN_TRAIN_SAMPLES:
        log.warning(
            "behavior_classifier.train skipped: %d samples (min %d)",
            len(X_rows), settings.ML_MIN_TRAIN_SAMPLES,
        )
        return {
            "status": "skipped",
            "reason": "insufficient_data",
            "n": len(X_rows),
        }

    X = np.array(X_rows, dtype=np.float32)
    le = LabelEncoder()
    y = le.fit_transform(y_labels)

    # ------------------------------------------------------------------
    # 4. Train / eval split (80/20)
    # ------------------------------------------------------------------
    split_idx = max(1, int(len(X) * 0.8))
    X_train, X_test = X[:split_idx], X[split_idx:]
    y_train, y_test = y[:split_idx], y[split_idx:]

    model = RandomForestClassifier(
        n_estimators=100,
        max_depth=6,
        min_samples_leaf=3,
        class_weight="balanced",
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_train, y_train)

    # ------------------------------------------------------------------
    # 5. Evaluate
    # ------------------------------------------------------------------
    y_pred = model.predict(X_test)
    acc = float(accuracy_score(y_test, y_pred)) if len(y_test) else 0.0
    f1 = float(
        f1_score(y_test, y_pred, average="weighted", zero_division=0)
    ) if len(y_test) else 0.0

    log.info(
        "behavior_classifier.trained n=%d acc=%.4f f1=%.4f",
        len(X), acc, f1,
    )

    # ------------------------------------------------------------------
    # 6. Serialize
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
        {
            "model": model,
            "label_encoder": le,
            "feature_names": BEHAVIOR_FEATURE_NAMES,
        },
        artifact_path,
    )

    # ------------------------------------------------------------------
    # 7. Register
    # ------------------------------------------------------------------
    metrics = {
        "accuracy": round(acc, 6),
        "f1_weighted": round(f1, 6),
        "n_train": int(split_idx),
        "n_test": int(len(X) - split_idx),
        "classes": le.classes_.tolist(),
    }
    reg.register(
        model_name=MODEL_NAME,
        version=version,
        artifact_path=artifact_path,
        metrics=metrics,
        feature_names=BEHAVIOR_FEATURE_NAMES,
        session=session,
    )
    session.commit()

    return {"status": "ok", "version": version, **metrics}
