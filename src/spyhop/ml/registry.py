"""Model registry helpers — thin wrappers around the MLModelRegistry table.

All functions take a sync SQLAlchemy session (psycopg2 / Celery-safe).
Promotion is atomic: active → retired, shadow → active in one transaction.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from spyhop.db.models import MLModelRegistry


def register(
    model_name: str,
    version: str,
    artifact_path: str,
    metrics: dict[str, Any],
    feature_names: list[str],
    session: Session,
) -> MLModelRegistry:
    """Insert a new registry row with status='shadow'."""
    row = MLModelRegistry(
        model_name=model_name,
        version=version,
        artifact_path=artifact_path,
        metrics_json=metrics,
        feature_names_json=feature_names,
        status="shadow",
        trained_at=datetime.now(timezone.utc),
    )
    session.add(row)
    session.flush()
    return row


def get_active(model_name: str, session: Session) -> MLModelRegistry | None:
    """Return the single active model row, or None."""
    return session.execute(
        select(MLModelRegistry).where(
            MLModelRegistry.model_name == model_name,
            MLModelRegistry.status == "active",
        )
    ).scalar_one_or_none()


def get_shadow(model_name: str, session: Session) -> MLModelRegistry | None:
    """Return the most recently trained shadow model, or None."""
    return session.execute(
        select(MLModelRegistry)
        .where(
            MLModelRegistry.model_name == model_name,
            MLModelRegistry.status == "shadow",
        )
        .order_by(MLModelRegistry.trained_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def promote(registry_id: int, session: Session) -> None:
    """Atomically retire the current active model and promote shadow → active."""
    now = datetime.now(timezone.utc)

    # Find the row being promoted
    target = session.get(MLModelRegistry, registry_id)
    if target is None:
        raise ValueError(f"No registry row with id={registry_id}")

    # Retire existing active (if any)
    current_active = get_active(target.model_name, session)
    if current_active is not None:
        current_active.status = "retired"
        current_active.retired_at = now

    target.status = "active"
    target.promoted_at = now
    session.flush()


def retire(registry_id: int, session: Session) -> None:
    now = datetime.now(timezone.utc)
    row = session.get(MLModelRegistry, registry_id)
    if row is not None:
        row.status = "retired"
        row.retired_at = now
        session.flush()


def ensure_artifact_dir(artifact_path: str) -> None:
    """Create parent directory for an artifact file if it doesn't exist."""
    os.makedirs(os.path.dirname(artifact_path), exist_ok=True)
