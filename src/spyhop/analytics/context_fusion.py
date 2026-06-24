"""Contextual Fusion — Environmental and Registry data enrichment.

Two independent data streams are fused with each AIS ping:

1. ENVIRONMENTAL (from EnvironmentRaster grid):
   - Sea Surface Temperature (SST): tuna aggregate at thermal fronts (15-28°C
     contact zones). Loitering at such a front raises fishing suspicion.
   - Wave height / wind speed: storm conditions explain apparent drifting without
     implying illegal activity (the vessel is heaving-to for safety).

2. REGISTRY (from Redis MMSI profile cache):
   - Verified vessel type from authoritative registry (IHS/Equasis).
     If the AIS-reported type contradicts the registered type, the operator
     likely falsified the type field to avoid scrutiny.
   - Historical risk score: repeat offenders (vessels that scored high in prior
     cycles) warrant heightened vigilance.

Caching strategy:
  - Environmental raster: hourly Celery beat task upserts a global grid into
    PostGIS. Per-vessel lookup is a single ST_DWithin nearest-neighbour query.
  - MMSI profile: first-seen vessels trigger a registry lookup (or DB history
    query); result cached in Redis for 30 days (PROFILE_CACHE_TTL_DAYS).

References:
  Zainuddin et al. (2017) "Thermal fronts and albacore tuna catch" — SST front
  Copernicus Marine Service CMEMS — SST and wave-height products (GRIB/NetCDF)
  IHS Markit / Equasis — authoritative ship registry
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROFILE_CACHE_TTL_DAYS = 30
PROFILE_CACHE_TTL_S = PROFILE_CACHE_TTL_DAYS * 86400

# Tuna-optimal temperature range (°C): yellowfin/albacore aggregate at the
# warm side of temperature fronts in this band.
TUNA_SST_MIN = 15.0
TUNA_SST_MAX = 28.0

# A thermal front exists when adjacent raster cells differ by more than this
# amount over a ~50 km separation.
SST_FRONT_GRADIENT_THRESHOLD = 2.0  # °C

# Storm thresholds (WMO scale 7 = near-gale / gale).
STORM_WAVE_HEIGHT_M = 5.0
STORM_WIND_KN = 40.0

# Sentinel values matching the Vessel dataclass defaults.
SST_NO_DATA = -999.0
WAVE_NO_DATA = -1.0
WIND_NO_DATA = -1.0


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EnvironmentalContext:
    """Environmental conditions at a vessel's current position.

    All numeric fields use sentinel values when the raster has no coverage:
      sst_celsius        = SST_NO_DATA (-999)
      wave_height_m      = WAVE_NO_DATA (-1)
      wind_speed_kn      = WIND_NO_DATA (-1)
    """

    sst_celsius: float = SST_NO_DATA
    wave_height_m: float = WAVE_NO_DATA
    wind_speed_kn: float = WIND_NO_DATA
    sst_at_thermal_front: bool = False      # SST gradient > threshold detected
    raster_age_hours: float = -1.0          # hours since raster was last updated


@dataclass(frozen=True)
class VesselProfile:
    """Registry-enriched identity for a vessel (Redis-cached).

    verified_type    — official ship type from IHS/Equasis (empty = not looked up)
    home_port        — registered port of call (empty = not available)
    historical_risk  — highest risk score seen in the last 30 days; -1 = new vessel
    """

    verified_type: str = ""
    home_port: str = ""
    historical_risk: float = -1.0
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Environmental helpers
# ---------------------------------------------------------------------------


def in_tuna_thermal_range(sst: float) -> bool:
    """True when SST is in the productive tuna-aggregation band."""
    if sst == SST_NO_DATA:
        return False
    return TUNA_SST_MIN <= sst <= TUNA_SST_MAX


def is_storm_conditions(wave_m: float, wind_kn: float) -> bool:
    """True when wave height OR wind speed exceeds storm threshold.

    A vessel drifting under these conditions is most likely heaving-to for
    safety, not conducting an illegal rendezvous.
    """
    if wave_m != WAVE_NO_DATA and wave_m >= STORM_WAVE_HEIGHT_M:
        return True
    if wind_kn != WIND_NO_DATA and wind_kn >= STORM_WIND_KN:
        return True
    return False


def detect_sst_front(sst_at_vessel: float, nearby_sst_values: list[float]) -> bool:
    """True when a significant temperature gradient is present near the vessel.

    Compares the vessel's SST to surrounding raster-cell values. A front is
    identified when any neighbour differs by ≥ SST_FRONT_GRADIENT_THRESHOLD.
    """
    if sst_at_vessel == SST_NO_DATA or not nearby_sst_values:
        return False
    return any(
        abs(sst_at_vessel - nb) >= SST_FRONT_GRADIENT_THRESHOLD
        for nb in nearby_sst_values
        if nb != SST_NO_DATA
    )


# ---------------------------------------------------------------------------
# Registry type-mismatch classification
# ---------------------------------------------------------------------------

# Broad AIS fishing types — the AIS Ship Type code ranges for fishing vessels.
# If verified_type is fishing but AIS says cargo/tanker etc., that's a red flag.
_FISHING_VERIFIED_KEYWORDS = frozenset({
    "fishing", "trawler", "seiner", "longliner",
    "pole and line", "jigging", "dredger",
})

_NON_FISHING_AIS_KEYWORDS = frozenset({
    "cargo", "tanker", "container", "bulk", "general cargo",
    "ro-ro", "passenger", "reefer",
})


def type_mismatch_severity(ais_type: str, verified_type: str) -> Optional[str]:
    """Return a severity label if registry type contradicts AIS type.

    Returns:
      "critical" — verified=fishing but AIS says cargo/tanker (most common
                   evasion: a trawler masquerades as a merchant ship)
      "minor"    — type difference is within the same broad category
      None       — no meaningful mismatch or insufficient data
    """
    if not verified_type or not ais_type:
        return None

    vt = verified_type.lower()
    at = ais_type.lower()

    if vt == at:
        return None

    verified_is_fishing = any(kw in vt for kw in _FISHING_VERIFIED_KEYWORDS)
    ais_is_non_fishing = any(kw in at for kw in _NON_FISHING_AIS_KEYWORDS)

    if verified_is_fishing and ais_is_non_fishing:
        return "critical"

    # Less specific: just a different type altogether
    if vt not in at and at not in vt:
        return "minor"

    return None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    R = 6371.0
    dl = math.radians(lat2 - lat1)
    dlo = math.radians(lon2 - lon1)
    a = math.sin(dl / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(
        math.radians(lat2)
    ) * math.sin(dlo / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))
