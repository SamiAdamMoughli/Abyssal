"""SQLAlchemy ORM models for the Spyhop production database.

Three tables:
  vessel_positions   — live AIS snapshot + pre-computed risk score (PostGIS)
  iuu_blacklist      — authoritative IUU vessel records (CCAMLR / RFMO / TMT)
  sanctioned_vessels — OpenSanctions vessel entities
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from geoalchemy2 import Geometry, WKBElement
from geoalchemy2.shape import to_shape
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
# VesselPosition
# ---------------------------------------------------------------------------


class VesselPosition(Base):
    """One row per vessel (upserted on MMSI key) — live AIS snapshot.

    The ``position`` column is a PostGIS POINT(lon lat) in WGS-84 (SRID 4326).
    A GiST index on that column makes ST_Within / ST_DWithin run at <10ms even
    with millions of rows.
    """

    __tablename__ = "vessel_positions"
    __table_args__ = (
        Index("idx_vessel_positions_gist", "position", postgresql_using="gist"),
        Index("idx_vessel_positions_score", "risk_score"),
        UniqueConstraint("mmsi", name="uq_vessel_positions_mmsi"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # --- Identity ---
    mmsi: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    imo: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False, default="")

    # --- PostGIS geometry: POINT(lon lat) SRID=4326 -------------------------
    position: Mapped[WKBElement] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326, spatial_index=False),
        nullable=False,
    )

    # --- Kinematic / behavioural fields (mirroring Vessel dataclass) ---------
    speed_knots: Mapped[float] = mapped_column(Float, default=0.0)
    flag: Mapped[str] = mapped_column(String(10), default="UNK")
    vessel_type: Mapped[str] = mapped_column(String(50), default="unknown")
    ais_gap_hours: Mapped[float] = mapped_column(Float, default=0.0)
    loitering_hours: Mapped[float] = mapped_column(Float, default=0.0)
    in_protected_area: Mapped[bool] = mapped_column(Boolean, default=False)

    # Transhipment signals
    recent_port_calls: Mapped[int] = mapped_column(Integer, default=-1)
    days_since_port: Mapped[float] = mapped_column(Float, default=-1.0)
    distance_to_nearest_port_nm: Mapped[float] = mapped_column(Float, default=-1.0)
    nearby_fishing_vessels: Mapped[int] = mapped_column(Integer, default=0)
    rendezvous_duration_hours: Mapped[float] = mapped_column(Float, default=0.0)
    ais_vessel_class: Mapped[str] = mapped_column(String(10), default="")

    # --- Risk output ---------------------------------------------------------
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    top_reason_label: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    reasons_json: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)

    # --- Metadata ------------------------------------------------------------
    data_source: Mapped[str] = mapped_column(String(20), default="synthetic")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=_now_utc,
    )

    # --- Computed properties (lat/lon from PostGIS geometry) ----------------

    @property
    def lat(self) -> float:
        """Latitude extracted from PostGIS WKB without extra DB round-trip."""
        return float(to_shape(self.position).y)

    @property
    def lon(self) -> float:
        """Longitude extracted from PostGIS WKB without extra DB round-trip."""
        return float(to_shape(self.position).x)

    def to_dict(self) -> dict[str, Any]:
        reasons = self.reasons_json or []
        if isinstance(reasons, str):
            reasons = json.loads(reasons)
        return {
            "mmsi": self.mmsi,
            "imo": self.imo,
            "name": self.name,
            "lat": self.lat,
            "lon": self.lon,
            "speed_knots": self.speed_knots,
            "flag": self.flag,
            "vessel_type": self.vessel_type,
            "ais_gap_hours": self.ais_gap_hours,
            "loitering_hours": self.loitering_hours,
            "in_protected_area": self.in_protected_area,
            "risk_score": self.risk_score,
            "top_reason_label": self.top_reason_label,
            "reasons": reasons,
            "data_source": self.data_source,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ---------------------------------------------------------------------------
# IUUBlacklist
# ---------------------------------------------------------------------------


class IUUBlacklist(Base):
    """Authoritative IUU vessel records (CCAMLR / RFMO / TMT).

    Refreshed daily by the ``sync_iuu_list`` Celery task.
    The combination (source, mmsi, imo, name) uniquely identifies a listing.
    """

    __tablename__ = "iuu_blacklist"
    __table_args__ = (
        Index("idx_iuu_mmsi", "mmsi"),
        Index("idx_iuu_imo", "imo"),
        Index("idx_iuu_name_trgm", "vessel_name", postgresql_using="gin",
              postgresql_ops={"vessel_name": "gin_trgm_ops"}),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_source: Mapped[str] = mapped_column(String(50), nullable=False)  # CCAMLR/RFMO/TMT
    mmsi: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    imo: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    vessel_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    aliases_json: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    flag: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    listing_year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    raw_json: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=_now_utc
    )


# ---------------------------------------------------------------------------
# SanctionedVessel
# ---------------------------------------------------------------------------


class SanctionedVessel(Base):
    """OpenSanctions vessel entities (CC BY 4.0 NonCommercial).

    Refreshed daily by the ``sync_sanctions`` Celery task.
    Unique key is the OpenSanctions entity ID.
    """

    __tablename__ = "sanctioned_vessels"
    __table_args__ = (
        Index("idx_sanctions_mmsi", "mmsi"),
        Index("idx_sanctions_imo", "imo"),
        Index("idx_sanctions_name_trgm", "vessel_name", postgresql_using="gin",
              postgresql_ops={"vessel_name": "gin_trgm_ops"}),
        UniqueConstraint("opensanctions_id", name="uq_sanctioned_vessels_os_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    opensanctions_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    vessel_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    aliases_json: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    mmsi: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    imo: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    flag: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    sanctions_datasets: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=_now_utc
    )
