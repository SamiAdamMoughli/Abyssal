"""SQLAlchemy ORM models for the Spyhop production database.

Four tables:
  vessel_positions   — live AIS snapshot + pre-computed risk score (PostGIS)
  vessel_tracks      — position history for motion profile analysis
  iuu_blacklist      — authoritative IUU vessel records (CCAMLR / RFMO / TMT)
  sanctioned_vessels — OpenSanctions vessel entities
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any, Optional

from geoalchemy2 import Geometry, WKBElement
from geoalchemy2.shape import to_shape
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    PrimaryKeyConstraint,
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
    flag: Mapped[str] = mapped_column(String(20), default="UNK")
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

    # --- H3 spatial index (resolution 7, ~5 km² per cell) -------------------
    h3_index: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True, index=True
    )

    # --- GFW vessel registry enrichment (from fishing-vessels-v3) -----------
    gfw_geartype: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )
    gfw_flag: Mapped[Optional[str]] = mapped_column(
        String(10), nullable=True
    )
    gfw_length_m: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )
    gfw_engine_kw: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )
    gfw_tonnage_gt: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )
    gfw_fishing_hours: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )
    gfw_active_hours: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )
    gfw_registries: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    gfw_self_reported_fishing: Mapped[Optional[bool]] = mapped_column(
        Boolean, nullable=True
    )

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
            "h3_index": self.h3_index,
            "gfw_geartype": self.gfw_geartype,
            "gfw_flag": self.gfw_flag,
            "gfw_length_m": self.gfw_length_m,
            "gfw_engine_kw": self.gfw_engine_kw,
            "gfw_tonnage_gt": self.gfw_tonnage_gt,
            "gfw_fishing_hours": self.gfw_fishing_hours,
            "gfw_active_hours": self.gfw_active_hours,
            "gfw_registries": self.gfw_registries,
            "gfw_self_reported_fishing": self.gfw_self_reported_fishing,
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
        # BRIN on timestamp for fast prune (DELETE WHERE timestamp < cutoff).
        # The GiST on position was dropped in migration 0010 — no query uses it.
        Index("idx_vessel_tracks_ts_brin", "timestamp", postgresql_using="brin"),
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


# ---------------------------------------------------------------------------
# VesselPositionSnapshot
# ---------------------------------------------------------------------------


class VesselPositionSnapshot(Base):
    """Append-only hourly snapshot of vessel_positions for corridor analysis.

    Written by ``snapshot_vessel_positions`` every hour. Pruned after 90 days
    by ``prune_vessel_snapshots``. The h3_index_5 column (H3 res-5 parent of
    h3_index_7) is the primary grouping key for weekly corridor materialization.
    """

    __tablename__ = "vessel_position_snapshots"
    __table_args__ = (
        Index("idx_snapshots_h3_5_snapped", "h3_index_5", "snapped_at"),
        Index("idx_snapshots_mmsi_snapped", "mmsi", "snapped_at"),
        Index("idx_snapshots_snapped", "snapped_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mmsi: Mapped[str] = mapped_column(String(20), nullable=False)
    h3_index_7: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    h3_index_5: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    risk_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    flag: Mapped[str] = mapped_column(String(10), nullable=False, default="UNK")
    vessel_type: Mapped[str] = mapped_column(String(50), nullable=False, default="unknown")
    ais_gap_hours: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    loitering_hours: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    in_protected_area: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rendezvous_duration_hours: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    spoofing_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    snapped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# ---------------------------------------------------------------------------
# H3RiskCorridor
# ---------------------------------------------------------------------------


class H3RiskCorridor(Base):
    """Weekly H3 res-5 risk corridor aggregate.

    Materialized every Sunday at 01:00 UTC by ``materialize_h3_corridors``.
    Primary key is (h3_cell, week_start) so each run is idempotently upserted.

    corridor_score is the composite ranking signal:
      (high_risk*3 + med_risk + dark*2 + rendezvous*2.5 + mpa*2) × √persistence_weeks
    A high corridor_score on a cell that recurs across many weeks is the
    structural corridor signal — distinguishing smuggling routes from noise.
    """

    __tablename__ = "h3_risk_corridors"
    __table_args__ = (
        PrimaryKeyConstraint("h3_cell", "week_start", name="pk_h3_risk_corridors"),
        Index("idx_h3_corridors_score", "corridor_score"),
        Index("idx_h3_corridors_week", "week_start"),
    )

    h3_cell: Mapped[str] = mapped_column(String(20), nullable=False, primary_key=True)
    week_start: Mapped[date] = mapped_column(Date, nullable=False, primary_key=True)

    vessel_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    high_risk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    med_risk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dark_vessel_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rendezvous_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mpa_incursion_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    avg_risk_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    max_risk_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    dominant_flag: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    dominant_vessel_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Persistence = distinct weeks across full history where high_risk_count > 0
    persistence_weeks: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    corridor_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    materialized_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# GFWVesselRegistry
# ---------------------------------------------------------------------------


class GFWVesselRegistry(Base):
    """GFW fishing-vessels-v3 registry — one row per (mmsi, year).

    Loaded once from the static CSV via the load_gfw_vessel_registry task.
    Query via the gfw_vessel_latest materialized view for the most recent
    record per MMSI.
    """

    __tablename__ = "gfw_vessel_registry"
    __table_args__ = (
        PrimaryKeyConstraint("mmsi", "year", name="pk_gfw_vessel_registry"),
        Index("idx_gfw_registry_mmsi", "mmsi"),
    )

    mmsi: Mapped[str] = mapped_column(String(20), nullable=False, primary_key=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False, primary_key=True)

    flag_ais: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    flag_registry: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    flag_gfw: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    vessel_class_inferred: Mapped[Optional[str]] = mapped_column(
        String(200), nullable=True
    )
    vessel_class_inferred_score: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )
    vessel_class_registry: Mapped[Optional[str]] = mapped_column(
        String(200), nullable=True
    )
    vessel_class_gfw: Mapped[Optional[str]] = mapped_column(
        String(200), nullable=True
    )

    self_reported_fishing_vessel: Mapped[Optional[bool]] = mapped_column(
        Boolean, nullable=True
    )

    length_m_gfw: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    engine_power_kw_gfw: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    tonnage_gt_gfw: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    registries_listed: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    active_hours: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fishing_hours: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


# ---------------------------------------------------------------------------
# MLModelRegistry
# ---------------------------------------------------------------------------


class MLModelRegistry(Base):
    """One row per trained model version.

    Lifecycle: shadow → active → retired.
    Only one row per (model_name) may hold status='active' at a time;
    the promote helper in ml.registry enforces this atomically.

    artifact_path points to a joblib file inside the model-artifacts volume,
    e.g. /app/model_artifacts/risk_scorer/1.0.0/model.joblib
    """

    __tablename__ = "ml_model_registry"
    __table_args__ = (
        UniqueConstraint("model_name", "version", name="uq_ml_model_version"),
        Index("idx_ml_registry_name_status", "model_name", "status"),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(20), nullable=False)
    artifact_path: Mapped[str] = mapped_column(Text, nullable=False)
    # {"mae": 0.05, "r2": 0.92, "n_train": 1200,
    #  "score_histogram": [0.1, 0.2, ...]}
    metrics_json: Mapped[Optional[Any]] = mapped_column(
        JSONB, nullable=True, default=dict
    )
    # ordered feature name list used at training time
    feature_names_json: Mapped[Optional[Any]] = mapped_column(
        JSONB, nullable=True, default=list
    )
    # shadow / active / retired
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="shadow"
    )
    trained_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    promoted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retired_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# ---------------------------------------------------------------------------
# MLPredictionLog
# ---------------------------------------------------------------------------


class MLPredictionLog(Base):
    """Append-only log of shadow/active model predictions.

    Used by the drift monitor to compare the current score distribution
    against the training-time baseline stored in MLModelRegistry.metrics_json.
    Rows older than 30 days are pruned by the ml.prune_prediction_log task.
    """

    __tablename__ = "ml_prediction_log"
    __table_args__ = (
        Index("idx_ml_pred_log_created", "created_at"),
        Index("idx_ml_pred_log_mmsi_model", "mmsi", "model_name"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(20), nullable=False)
    mmsi: Mapped[str] = mapped_column(String(20), nullable=False)
    predicted_score: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
