"""Pydantic v2 request/response schemas for the Spyhop API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Shared / primitive schemas
# ---------------------------------------------------------------------------


class RiskReasonSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    points: float
    label: str
    detail: str
    evidence_type: str = "heuristic"


class VesselSchema(BaseModel):
    """Single vessel response — mirrors VesselPosition.to_dict()."""

    model_config = ConfigDict(from_attributes=True)

    mmsi: str
    imo: Optional[str] = None
    name: str
    lat: float
    lon: float
    speed_knots: float = 0.0
    flag: str = "UNK"
    vessel_type: str = "unknown"
    ais_gap_hours: float = 0.0
    loitering_hours: float = 0.0
    in_protected_area: bool = False
    risk_score: float = 0.0
    top_reason_label: Optional[str] = None
    reasons: list[dict[str, Any]] = Field(default_factory=list)
    data_source: str = "synthetic"
    updated_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Collection responses
# ---------------------------------------------------------------------------


class VesselListResponse(BaseModel):
    source: str
    count: int
    vessels: list[VesselSchema]


class TopTargetsResponse(BaseModel):
    source: str
    count: int
    targets: list[VesselSchema]


# ---------------------------------------------------------------------------
# Query parameter helpers (validated, typed)
# ---------------------------------------------------------------------------


class BboxParams(BaseModel):
    """Validated bounding-box query params."""

    min_lat: float = Field(..., ge=-90, le=90)
    max_lat: float = Field(..., ge=-90, le=90)
    min_lon: float = Field(..., ge=-180, le=180)
    max_lon: float = Field(..., ge=-180, le=180)

    @field_validator("max_lat")
    @classmethod
    def max_lat_gt_min(cls, v: float, info: Any) -> float:
        if "min_lat" in info.data and v <= info.data["min_lat"]:
            raise ValueError("max_lat must be greater than min_lat")
        return v

    @field_validator("max_lon")
    @classmethod
    def max_lon_gt_min(cls, v: float, info: Any) -> float:
        if "min_lon" in info.data and v <= info.data["min_lon"]:
            raise ValueError("max_lon must be greater than min_lon")
        return v

    @property
    def as_tuple(self) -> tuple[float, float, float, float]:
        """(min_lon, min_lat, max_lon, max_lat) — PostGIS convention."""
        return (self.min_lon, self.min_lat, self.max_lon, self.max_lat)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    db: str
    redis: str
