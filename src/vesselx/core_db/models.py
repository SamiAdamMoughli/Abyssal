"""VesselX-specific ORM models — alert log and field deployment registry.

These tables complement the spyhop base schema (vessel_positions, vessel_tracks,
iuu_blacklist, sanctioned_vessels) with VesselX operational tables:

  vesselx_alerts      — persisted findings from the brain rule evaluator.
                        Written by brain.tasks; read by the desktop client
                        alert sidebar and investigation timeline.

  field_deployments   — registry of offline-capable shipboard nodes.
                        Each vessel-mounted mini-PC running VesselX in
                        offline-first mode has one row here for per-node
                        sync state tracking.

Both tables share the same SQLAlchemy Base as the spyhop models so they
participate in the same Alembic migration chain.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from spyhop.db.engine import Base


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# VesselXAlert
# ---------------------------------------------------------------------------


class VesselXAlert(Base):
    """One row per brain rule evaluation finding.

    alert_id is a client-generated UUID so the brain worker can write it
    without a DB round-trip; the unique constraint makes ingestion idempotent.
    """

    __tablename__ = "vesselx_alerts"
    __table_args__ = (
        Index("idx_vxa_mmsi",       "mmsi"),
        Index("idx_vxa_rule",       "rule_id"),
        Index("idx_vxa_severity",   "severity"),
        Index("idx_vxa_triggered",  "triggered_at"),
        UniqueConstraint("alert_id", name="uq_vesselx_alerts_id"),
    )

    id:         Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_id:   Mapped[str] = mapped_column(String(36),  nullable=False, unique=True)
    rule_id:    Mapped[str] = mapped_column(String(50),  nullable=False)
    rule_label: Mapped[str] = mapped_column(String(100), nullable=False)
    severity:   Mapped[str] = mapped_column(String(20),  nullable=False)
    message:    Mapped[str] = mapped_column(Text,        nullable=False)

    mmsi:       Mapped[Optional[str]]   = mapped_column(String(20),  nullable=True)
    lat:        Mapped[Optional[float]] = mapped_column(Float,       nullable=True)
    lon:        Mapped[Optional[float]] = mapped_column(Float,       nullable=True)
    h3_index:   Mapped[Optional[str]]   = mapped_column(String(20),  nullable=True)

    acknowledged:    Mapped[bool]          = mapped_column(Boolean, default=False)
    acknowledged_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "alert_id":       self.alert_id,
            "rule_id":        self.rule_id,
            "rule_label":     self.rule_label,
            "severity":       self.severity,
            "message":        self.message,
            "mmsi":           self.mmsi,
            "lat":            self.lat,
            "lon":            self.lon,
            "h3_index":       self.h3_index,
            "acknowledged":   self.acknowledged,
            "triggered_at":   self.triggered_at.isoformat() if self.triggered_at else None,
        }


# ---------------------------------------------------------------------------
# FieldDeployment
# ---------------------------------------------------------------------------


class FieldDeployment(Base):
    """Registry of offline-capable shipboard mini-PC nodes.

    Each node has a unique ``node_id`` (set in the ship's config file).
    The ``last_sync_sequence`` tracks which SyncDelta sequence the cloud DB
    has already ingested so the sync worker can resume without re-uploading.
    """

    __tablename__ = "field_deployments"
    __table_args__ = (
        UniqueConstraint("node_id", name="uq_field_deployments_node"),
        Index("idx_fd_vessel_mmsi", "vessel_mmsi"),
    )

    id:           Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id:      Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    vessel_mmsi:  Mapped[Optional[str]] = mapped_column(String(20),  nullable=True)
    vessel_name:  Mapped[Optional[str]] = mapped_column(String(150), nullable=True)

    last_seen_at:      Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_sequence: Mapped[int]              = mapped_column(Integer, default=0)
    operational_area:  Mapped[Optional[str]]     = mapped_column(Text, nullable=True)

    metadata_json: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=_now_utc,
    )
