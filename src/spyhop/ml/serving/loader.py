"""Hot-reloadable model loader for use inside Celery workers.

ModelLoader is a per-model-name singleton.  On first call to predict(),
it loads the active model artifact from the registry via a sync DB query.
After that, it checks a Redis version key every VERSION_CHECK_INTERVAL_S
seconds (default 300 = 5 min) to detect promotions without worker restart.

Thread-safe: an RLock guards the lazy-load and reload paths.

Usage (inside a Celery task):
    from spyhop.ml.serving.loader import get_loader
    loader = get_loader("risk_scorer", session, redis)
    score, version = loader.predict_with_version(features)
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

log = logging.getLogger(__name__)

VERSION_CHECK_INTERVAL_S = 300
REDIS_VERSION_KEY = "ml:model_version:{model_name}"


class ModelLoader:
    """Lazy-loading, version-aware model wrapper."""

    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        self._model: Any = None
        self._feature_names: list[str] = []
        self._version: str | None = None
        self._last_version_check: float = 0.0
        self._lock = threading.RLock()

    def predict_with_version(
        self,
        features: list[float],
        session: Any = None,
        redis_client: Any = None,
    ) -> tuple[float, str]:
        """Return (predicted_score, version).  Returns (-1.0, '') if not ready."""
        with self._lock:
            self._maybe_reload(session, redis_client)
            if self._model is None:
                return -1.0, ""
            try:
                import numpy as np
                x = np.array([features], dtype=np.float32)
                score = float(self._model.predict(x)[0])
                score = max(0.0, min(1.0, score))
                return score, self._version or ""
            except Exception as exc:
                log.error(
                    "loader.predict_error model=%s err=%s",
                    self._model_name, exc,
                )
                return -1.0, ""

    def reload(self, session: Any = None) -> bool:
        """Force reload the active model from the registry. Returns True on success."""
        with self._lock:
            return self._load_active(session)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _maybe_reload(self, session: Any, redis_client: Any) -> None:
        now = time.monotonic()
        if now - self._last_version_check < VERSION_CHECK_INTERVAL_S:
            return
        self._last_version_check = now

        if redis_client is None:
            if self._model is None:
                self._load_active(session)
            return

        try:
            key = REDIS_VERSION_KEY.format(model_name=self._model_name)
            remote_version = redis_client.get(key)
            if remote_version and remote_version != self._version:
                log.info(
                    "loader.version_changed model=%s old=%s new=%s",
                    self._model_name, self._version, remote_version,
                )
                self._load_active(session)
        except Exception as exc:
            log.warning("loader.version_check_error err=%s", exc)
            if self._model is None:
                self._load_active(session)

    def _load_active(self, session: Any) -> bool:
        """Load the active model artifact from the registry."""
        if session is None:
            return False
        try:
            import joblib
            from sqlalchemy import select
            from spyhop.db.models import MLModelRegistry

            row = session.execute(
                select(MLModelRegistry).where(
                    MLModelRegistry.model_name == self._model_name,
                    MLModelRegistry.status == "active",
                )
            ).scalar_one_or_none()

            if row is None:
                log.debug(
                    "loader.no_active_model model=%s", self._model_name
                )
                return False

            artifact = joblib.load(row.artifact_path)
            self._model = artifact["model"]
            self._feature_names = artifact.get("feature_names", [])
            self._version = row.version
            log.info(
                "loader.loaded model=%s version=%s",
                self._model_name, self._version,
            )
            return True
        except Exception as exc:
            log.error(
                "loader.load_error model=%s err=%s", self._model_name, exc
            )
            return False


# ---------------------------------------------------------------------------
# Module-level singleton registry
# ---------------------------------------------------------------------------

_loaders: dict[str, ModelLoader] = {}
_registry_lock = threading.Lock()


def get_loader(model_name: str) -> ModelLoader:
    """Return the shared ModelLoader instance for model_name."""
    with _registry_lock:
        if model_name not in _loaders:
            _loaders[model_name] = ModelLoader(model_name)
        return _loaders[model_name]
