"""Transport-neutral event contracts used between VesselX services."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


EventType = Literal[
    "telemetry.position.raw",
    "telemetry.position.normalized",
    "telemetry.position.spatialized",
    "telemetry.static.normalized",
    "sensor.sar.detection",
    "sensor.viirs.detection",
    "spatial.zone.entered",
    "spatial.zone.exited",
    "spatial.h3.context.updated",
    "analytics.vessel.scored",
    "analytics.alert.created",
    "analytics.dark_vessel.candidate",
    "analytics.identity_mismatch.detected",
    "registry.refresh.completed",
]


class EventSubject(BaseModel):
    """Identity block for the vessel or sensor object an event describes."""

    mmsi: str | None = None
    imo: str | None = None
    sensor_id: str | None = None


class GeoPoint(BaseModel):
    """GeoJSON Point geometry with coordinates in lon/lat order."""

    type: Literal["Point"] = "Point"
    coordinates: tuple[float, float]


class VesselXEvent(BaseModel):
    """Canonical event envelope for APIs, queues, and future Kafka topics."""

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    event_type: EventType
    occurred_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    source: str
    schema_version: int = 1
    subject: EventSubject = Field(default_factory=EventSubject)
    geometry: GeoPoint | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
