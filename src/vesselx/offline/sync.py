"""Offline-first sync primitives.

These models define how shipboard deployments can buffer track points and
analyst notes while disconnected, then replay idempotent deltas when cloud
connectivity returns.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


DeltaKind = Literal[
    "track_point",
    "vessel_state",
    "alert_ack",
    "analyst_note",
    "case_update",
]


class SyncDelta(BaseModel):
    """One idempotent local change queued for later upstream sync."""

    delta_id: str = Field(default_factory=lambda: str(uuid4()))
    kind: DeltaKind
    entity_id: str
    occurred_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    origin_node: str = "shipboard"
    sequence: int
    payload: dict[str, Any] = Field(default_factory=dict)


class SyncCheckpoint(BaseModel):
    """Last confirmed upstream sync position for a deployment node."""

    node_id: str
    last_sequence: int = 0
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
