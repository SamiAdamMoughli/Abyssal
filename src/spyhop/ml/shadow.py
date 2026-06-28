"""Shadow scoring engine.

Runs the current active (or shadow) ML model on a batch of vessel states
*after* rule evaluation completes, writing results to:

  Redis  ml:shadow:{mmsi}   JSON {version, score, ts}   TTL 600 s
  DB     ml_prediction_log  (batched insert, non-blocking to main eval path)

The function is intentionally fire-and-forget from the brain task's
perspective — it logs errors and never raises so it cannot interrupt alerts.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import ujson

if TYPE_CHECKING:
    from spyhop.ml.serving.loader import ModelLoader

log = logging.getLogger(__name__)

SHADOW_TTL = 600  # Redis key TTL in seconds


def score_batch(
    vessel_states: list[dict[str, Any]],
    loader: "ModelLoader",
    redis_client: Any,
    sync_session_factory: Any,
) -> None:
    """Score a batch of vessel states with the ML model.

    Writes Redis keys and bulk-inserts prediction log rows.
    Never raises — all errors are logged and swallowed.
    """
    from spyhop.ml.features import extract_from_state
    from spyhop.db.models import MLPredictionLog

    if not vessel_states:
        return

    try:
        predictions: list[tuple[str, float, str]] = []  # (mmsi, score, ver)

        for state in vessel_states:
            mmsi = state.get("mmsi", "")
            if not mmsi:
                continue
            try:
                features = extract_from_state(state)
                score, version = loader.predict_with_version(features)
                if score < 0:
                    continue
                predictions.append((mmsi, score, version))
            except Exception as exc:
                log.debug("shadow.score_error mmsi=%s err=%s", mmsi, exc)

        if not predictions:
            return

        now_iso = datetime.now(timezone.utc).isoformat()

        # Redis — pipeline all writes in one round-trip
        pipe = redis_client.pipeline(transaction=False)
        for mmsi, score, version in predictions:
            blob = ujson.dumps(
                {"version": version, "score": score, "ts": now_iso}
            )
            pipe.setex(f"ml:shadow:{mmsi}", SHADOW_TTL, blob)
        pipe.execute()

        # DB prediction log — bulk insert
        try:
            with sync_session_factory() as session:
                session.bulk_insert_mappings(
                    MLPredictionLog,
                    [
                        {
                            "model_name": "risk_scorer",
                            "version": version,
                            "mmsi": mmsi,
                            "predicted_score": score,
                        }
                        for mmsi, score, version in predictions
                    ],
                )
                session.commit()
        except Exception as exc:
            log.warning("shadow.db_log_error err=%s", exc)

        log.debug(
            "shadow.scored n=%d versions=%s",
            len(predictions),
            {v for _, _, v in predictions},
        )

    except Exception as exc:
        log.error("shadow.batch_error err=%s", exc)
