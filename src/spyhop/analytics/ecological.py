"""Ecological corridor and spawning-ground catalogue.

Pure data + date-arithmetic — no DB or Redis dependency. Imported by the
nightly ``refresh_ecological_masks`` Celery task, which pre-materialises
which H3 res-7 cells are ecologically active and writes the result to Redis.
The spatial worker then does a single HGETALL per cell in the hot path.

Seasonal windows use (month, day) tuples so they work across years without
baking in specific dates. Lunar-driven spawning events (coral broadcast
spawning) use a Julian Day Number approximation accurate to ±1 day.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SeasonWindow:
    """Inclusive calendar window, wraps across year-end if end < start."""
    start_month: int
    start_day:   int
    end_month:   int
    end_day:     int

    def contains(self, d: date) -> bool:
        start = date(d.year, self.start_month, self.start_day)
        end   = date(d.year, self.end_month,   self.end_day)
        if end < start:                        # wraps Dec → Jan
            return d >= start or d <= end
        return start <= d <= end

    def peak_fraction(self, d: date) -> float:
        """0.0 outside window, ramps 0→1→0 with cosine over the window."""
        if not self.contains(d):
            return 0.0
        start = date(d.year, self.start_month, self.start_day)
        end   = date(d.year, self.end_month,   self.end_day)
        if end < start:
            total = (date(d.year, 12, 31) - start).days + (end - date(d.year, 1, 1)).days + 1
            elapsed = (d - start).days if d >= start else (date(d.year, 12, 31) - start).days + (d - date(d.year, 1, 1)).days + 1
        else:
            total   = (end - start).days or 1
            elapsed = (d - start).days
        t = elapsed / total          # 0 → 1 over the season
        return 0.5 * (1 - math.cos(2 * math.pi * t))   # cosine hump


@dataclass(frozen=True)
class EcologicalCorridor:
    """A named cetacean or megafauna migration corridor segment."""
    id:                  str
    label:               str
    species:             tuple[str, ...]      # scientific names
    # Bounding box WGS-84
    south:               float
    north:               float
    west:                float
    east:                float
    season:              SeasonWindow
    # 0.0–1.0: reflects IUCN status × population size (1.0 = most endangered)
    endangerment_weight: float = 1.0


@dataclass(frozen=True)
class SpawningGround:
    """A marine spawning or aggregation zone."""
    id:             str
    label:          str
    species:        tuple[str, ...]
    south:          float
    north:          float
    west:           float
    east:           float
    active_months:  tuple[int, ...]    # months when potentially active
    lunar_driven:   bool = False       # True → check lunar phase
    # days after full moon when broadcast spawning begins (only if lunar_driven)
    lunar_offset_days: int  = 3
    lunar_window_days: int  = 5


# ---------------------------------------------------------------------------
# Catalogues
# ---------------------------------------------------------------------------

CETACEAN_CORRIDORS: list[EcologicalCorridor] = [

    # --- North Atlantic Right Whale (NARW) — Eubalaena glacialis -------------
    # ~360 individuals; single most endangered large whale on Earth.
    # NOAA Seasonal Management Area speed restrictions apply but are routinely
    # violated by AIS-dark vessels — primary target for this rule set.

    EcologicalCorridor(
        id="narw_calving_se_us",
        label="NARW Calving Grounds — SE United States",
        species=("Eubalaena glacialis",),
        south=28.0, north=32.0, west=-82.0, east=-75.0,
        season=SeasonWindow(12, 1, 3, 31),
        endangerment_weight=1.0,
    ),
    EcologicalCorridor(
        id="narw_mid_atlantic_transit",
        label="NARW Mid-Atlantic Transit Corridor",
        species=("Eubalaena glacialis",),
        south=35.0, north=41.0, west=-75.0, east=-68.0,
        season=SeasonWindow(3, 1, 5, 31),
        endangerment_weight=1.0,
    ),
    EcologicalCorridor(
        id="narw_gulf_of_maine",
        label="NARW Gulf of Maine Feeding Aggregation",
        species=("Eubalaena glacialis",),
        south=41.0, north=45.0, west=-71.0, east=-65.0,
        season=SeasonWindow(4, 1, 11, 30),
        endangerment_weight=1.0,
    ),
    EcologicalCorridor(
        id="narw_bay_of_fundy",
        label="NARW Bay of Fundy Critical Habitat",
        species=("Eubalaena glacialis",),
        south=44.0, north=47.0, west=-68.0, east=-63.0,
        season=SeasonWindow(6, 1, 11, 30),
        endangerment_weight=1.0,
    ),

    # --- Humpback Whale (Megaptera novaeangliae) — Eastern North Pacific ------

    EcologicalCorridor(
        id="humpback_monterey_feeding",
        label="Humpback Whale Monterey Bay Feeding Aggregation",
        species=("Megaptera novaeangliae",),
        south=36.0, north=38.5, west=-123.5, east=-121.5,
        season=SeasonWindow(7, 1, 12, 31),
        endangerment_weight=0.55,
    ),
    EcologicalCorridor(
        id="humpback_hawaii_calving",
        label="Humpback Whale Hawaiian Calving Grounds",
        species=("Megaptera novaeangliae",),
        south=20.0, north=22.5, west=-157.5, east=-155.5,
        season=SeasonWindow(11, 15, 4, 30),
        endangerment_weight=0.55,
    ),

    # --- Blue and Fin Whale — Gulf of St. Lawrence ---------------------------
    # Both species concentrate Jun–Oct; St. Lawrence Seaway is high-traffic.

    EcologicalCorridor(
        id="blue_fin_gulf_st_lawrence",
        label="Blue/Fin Whale Gulf of St. Lawrence Aggregation",
        species=("Balaenoptera musculus", "Balaenoptera physalus"),
        south=47.0, north=51.0, west=-68.0, east=-59.0,
        season=SeasonWindow(6, 1, 10, 31),
        endangerment_weight=0.9,
    ),

    # --- Whale Shark (Rhincodon typus) — Ningaloo Reef -----------------------
    # Filter feeders; near-zero collision avoidance; CITES Appendix II.

    EcologicalCorridor(
        id="whale_shark_ningaloo",
        label="Whale Shark Ningaloo Reef Aggregation",
        species=("Rhincodon typus",),
        south=-24.5, north=-21.5, west=112.5, east=115.0,
        season=SeasonWindow(3, 1, 7, 31),
        endangerment_weight=0.7,
    ),

    # --- Whale Shark (Rhincodon typus) — Yucatán Peninsula ------------------

    EcologicalCorridor(
        id="whale_shark_yucatan",
        label="Whale Shark Yucatán Aggregation",
        species=("Rhincodon typus",),
        south=20.5, north=22.5, west=-87.5, east=-86.0,
        season=SeasonWindow(6, 1, 9, 30),
        endangerment_weight=0.7,
    ),

    # --- Southern Ocean Humpback / Blue — Scotia Sea -------------------------

    EcologicalCorridor(
        id="southern_ocean_feeding",
        label="Southern Ocean Cetacean Feeding Grounds (Scotia Sea)",
        species=("Megaptera novaeangliae", "Balaenoptera musculus"),
        south=-65.0, north=-55.0, west=-70.0, east=-30.0,
        season=SeasonWindow(12, 1, 3, 31),
        endangerment_weight=0.7,
    ),
]


SPAWNING_GROUNDS: list[SpawningGround] = [

    # --- Coral Triangle — Banda Sea ------------------------------------------
    # World's highest marine biodiversity. Mass broadcast spawning 3–7 nights
    # after Oct/Nov full moon; fertilised eggs are surface-concentrated.

    SpawningGround(
        id="coral_triangle_banda_sea",
        label="Coral Triangle Broadcast Spawning — Banda Sea",
        species=("Acropora spp.", "Platygyra spp.", "Porites spp."),
        south=-8.0, north=-2.0, west=124.0, east=131.0,
        active_months=(10, 11),
        lunar_driven=True,
        lunar_offset_days=3,
        lunar_window_days=5,
    ),

    # --- Great Barrier Reef ---------------------------------------------------

    SpawningGround(
        id="gbr_coral_spawning",
        label="Great Barrier Reef Coral Mass Spawning",
        species=("Acropora millepora", "Goniastrea spp."),
        south=-24.0, north=-15.0, west=146.0, east=154.0,
        active_months=(10, 11),
        lunar_driven=True,
        lunar_offset_days=4,
        lunar_window_days=5,
    ),

    # --- Flower Garden Banks (Gulf of Mexico) --------------------------------

    SpawningGround(
        id="flower_garden_banks",
        label="Flower Garden Banks Coral Spawning",
        species=("Montastraea cavernosa", "Orbicella spp."),
        south=27.75, north=27.95, west=-93.9, east=-93.4,
        active_months=(8, 9),
        lunar_driven=True,
        lunar_offset_days=8,     # spawns ~8 days after Aug/Sep full moon
        lunar_window_days=3,
    ),

    # --- Coral Sea / Coral Triangle — Sulu-Sulawesi --------------------------

    SpawningGround(
        id="sulu_sulawesi_spawning",
        label="Sulu-Sulawesi Reef Fish Spawning Aggregations",
        species=("Epinephelus spp.", "Lutjanus spp."),
        south=3.0, north=10.0, west=118.0, east=127.0,
        active_months=(3, 4, 5, 10, 11),
        lunar_driven=False,
    ),
]


# ---------------------------------------------------------------------------
# Lunar phase helper
# ---------------------------------------------------------------------------

_LUNAR_CYCLE_DAYS = 29.53059
# Reference: known full moon 2000-01-20 18:02 UTC → JD 2451564.25
_FULL_MOON_REF_JD = 2451564.25


def _date_to_jd(d: date) -> float:
    """Approximate Julian Day Number for noon UTC on date d."""
    a = (14 - d.month) // 12
    y = d.year + 4800 - a
    m = d.month + 12 * a - 3
    return d.day + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32045


def lunar_age_days(d: date) -> float:
    """Days since last new moon (0=new, ~14.77=full)."""
    jd = _date_to_jd(d)
    days_since_ref_full = (jd - _FULL_MOON_REF_JD) % _LUNAR_CYCLE_DAYS
    # age since new moon = days_since_full + half_cycle
    age = (days_since_ref_full + _LUNAR_CYCLE_DAYS / 2) % _LUNAR_CYCLE_DAYS
    return age


def _days_after_full_moon(d: date) -> float:
    """Days elapsed since the most recent full moon (0 = full moon day)."""
    age = lunar_age_days(d)
    half = _LUNAR_CYCLE_DAYS / 2
    return (age - half) % _LUNAR_CYCLE_DAYS


# ---------------------------------------------------------------------------
# Active-zone queries (called by the nightly Celery task)
# ---------------------------------------------------------------------------

def active_corridors(d: date | None = None) -> list[EcologicalCorridor]:
    """Return corridors whose season contains ``d`` (default: today)."""
    if d is None:
        d = date.today()
    return [c for c in CETACEAN_CORRIDORS if c.season.contains(d)]


def is_spawning_active(ground: SpawningGround, d: date | None = None) -> bool:
    """True if ``ground`` is in an active spawning event on date ``d``."""
    if d is None:
        d = date.today()
    if d.month not in ground.active_months:
        return False
    if not ground.lunar_driven:
        return True
    days_post_full = _days_after_full_moon(d)
    return ground.lunar_offset_days <= days_post_full < (
        ground.lunar_offset_days + ground.lunar_window_days
    )


def active_spawning_grounds(d: date | None = None) -> list[SpawningGround]:
    """Return spawning grounds active on ``d``."""
    if d is None:
        d = date.today()
    return [g for g in SPAWNING_GROUNDS if is_spawning_active(g, d)]
