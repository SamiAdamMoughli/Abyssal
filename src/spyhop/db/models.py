"""SQLAlchemy ORM models for the Spyhop production database.

Four tables:
  vessel_positions   — live AIS snapshot + pre-computed risk score (PostGIS)
  vessel_tracks      — position history for motion profile analysis
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

    # --- Motion profile (computed from vessel_tracks sliding window) ---------
    behavior_status: Mapped[str] = mapped_column(
        String(20), default="unknown"
    )
    behavior_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    cog_degrees: Mapped[float] = mapped_column(Float, default=-1.0)

    # --- Spatial features (zone proximity, skirting, time in zone) ----------
    nearest_mpa_nm: Mapped[float] = mapped_column(Float, default=-1.0)
    time_in_zone_hours: Mapped[float] = mapped_column(Float, default=0.0)
    border_skirting: Mapped[bool] = mapped_column(Boolean, default=False)

    # --- Trajectory pattern (geometric fingerprint of 6-24 h route) --------
    trajectory_pattern: Mapped[str] = mapped_column(
        String(20), default="unknown"
    )
    trajectory_confidence: Mapped[float] = mapped_column(Float, default=0.0)

    # --- V2V encounter (populated by proximity detection each cycle) --------
    rendezvous_partner_type: Mapped[str] = mapped_column(
        String(30), default=""
    )
    rendezvous_meeting_class: Mapped[str] = mapped_column(
        String(30), default=""
    )

    # --- AIS gap kinematic analysis + spoofing signals ----------------------
    gap_type: Mapped[str] = mapped_column(String(20), default="")
    gap_displacement_nm: Mapped[float] = mapped_column(Float, default=-1.0)
    spoofing_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    spoofing_max_speed_kn: Mapped[float] = mapped_column(Float, default=0.0)

    # --- Contextual fusion (environmental raster + registry cache) ----------
    sst_celsius: Mapped[float] = mapped_column(Float, default=-999.0)
    wave_height_m: Mapped[float] = mapped_column(Float, default=-1.0)
    wind_speed_kn: Mapped[float] = mapped_column(Float, default=-1.0)
    sst_at_thermal_front: Mapped[bool] = mapped_column(Boolean, default=False)
    historical_risk_score: Mapped[float] = mapped_column(Float, default=-1.0)
    verified_vessel_type: Mapped[str] = mapped_column(String(50), default="")

    # --- Risk output ---------------------------------------------------------
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    top_reason_label: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )
    reasons_json: Mapped[Optional[Any]] = mapped_column(
        JSONB, nullable=True
    )

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
            "behavior_status": self.behavior_status,
            "behavior_confidence": self.behavior_confidence,
            "nearest_mpa_nm": self.nearest_mpa_nm,
            "time_in_zone_hours": self.time_in_zone_hours,
            "border_skirting": self.border_skirting,
            "trajectory_pattern": self.trajectory_pattern,
            "trajectory_confidence": self.trajectory_confidence,
            "rendezvous_partner_type": self.rendezvous_partner_type,
            "rendezvous_meeting_class": self.rendezvous_meeting_class,
            "gap_type": self.gap_type,
            "gap_displacement_nm": self.gap_displacement_nm,
            "spoofing_flag": self.spoofing_flag,
            "spoofing_max_speed_kn": self.spoofing_max_speed_kn,
            "sst_celsius": self.sst_celsius,
            "wave_height_m": self.wave_height_m,
            "wind_speed_kn": self.wind_speed_kn,
            "sst_at_thermal_front": self.sst_at_thermal_front,
            "historical_risk_score": self.historical_risk_score,
            "verified_vessel_type": self.verified_vessel_type,
            "risk_score": self.risk_score,
            "top_reason_label": self.top_reason_label,
            "reasons": reasons,
            "data_source": self.data_source,
            "updated_at": (
                self.updated_at.isoformat() if self.updated_at else None
            ),
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


# ---------------------------------------------------------------------------
# EnvironmentRaster
# ---------------------------------------------------------------------------


class EnvironmentRaster(Base):
    """Hourly environmental grid for contextual fusion.

    Each row represents one grid cell (~0.25° × 0.25° resolution, ~28 km).
    The ``position`` column is the cell centre as a PostGIS POINT.

    The hourly ``sync_environment_raster`` beat task downloads the latest CMEMS
    or NOAA products (GRIB/NetCDF), re-grids to 0.25° and upserts here.
    Per-vessel lookup: one ST_DWithin nearest-neighbour query against the
    GiST index (< 1 ms per vessel).

    Columns:
      sst_celsius   — Sea Surface Temperature (CMEMS SST_MED product or global)
      wave_height_m — Significant wave height Hs (CMEMS WAV product)
      wind_speed_kn — 10-m wind speed (converted from m/s)
      valid_time    — UTC timestamp of the forecast/analysis hour this row covers
    """

    __tablename__ = "environment_raster"
    __table_args__ = (
        Index(
            "idx_env_raster_gist", "position", postgresql_using="gist"
        ),
        Index("idx_env_raster_valid", "valid_time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    position: Mapped[WKBElement] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326, spatial_index=False),
        nullable=False,
    )
    sst_celsius: Mapped[float] = mapped_column(Float, nullable=False)
    wave_height_m: Mapped[float] = mapped_column(Float, nullable=False)
    wind_speed_kn: Mapped[float] = mapped_column(Float, nullable=False)
    valid_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    @property
    def lat(self) -> float:
        return float(to_shape(self.position).y)

    @property
    def lon(self) -> float:
        return float(to_shape(self.position).x)


# ---------------------------------------------------------------------------
# VesselTrack
# ---------------------------------------------------------------------------


class VesselTrack(Base):
    """Time-series of raw AIS pings per vessel — feeds motion profile analysis.

    One row per received ping. The sliding-window query pulls the last N pings
    for a given MMSI to compute SOG variance, COG turn rate, and tortuosity.
    Rows older than 7 days are pruned by the ``prune_vessel_tracks`` beat task.
    """

    __tablename__ = "vessel_tracks"
    __table_args__ = (
        # Composite index: all track queries filter by mmsi then sort by ts
        Index("idx_vessel_tracks_mmsi_ts", "mmsi", "timestamp"),
        Index(
            "idx_vessel_tracks_gist",
            "position",
            postgresql_using="gist",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    mmsi: Mapped[str] = mapped_column(String(20), nullable=False)
    position: Mapped[WKBElement] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326, spatial_index=False),
        nullable=False,
    )
    sog: Mapped[float] = mapped_column(Float, default=0.0)
    cog: Mapped[float] = mapped_column(Float, default=0.0)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    source: Mapped[str] = mapped_column(String(20), default="unknown")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
