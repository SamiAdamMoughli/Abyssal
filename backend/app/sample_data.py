"""Mission Radar - Datenquelle (Phase 1: synthetisch).

Dies ist die AUSTAUSCHBARE Schicht. Die Engine haengt nicht von dieser Datei ab,
sondern nur von der `Vessel`-Datenklasse. In Phase 2 wird diese Datei durch einen
echten Adapter ersetzt (z. B. Global Fishing Watch API), der ebenfalls
`List[Vessel]` liefert - die Engine bleibt unangetastet.

Der explizite `VesselSource`-Protokoll-Vertrag dokumentiert, was eine Datenquelle
erfuellen muss.

Vessel-Typ-Hierarchie (4 Kategorien / 14 Subtypen):
  Commercial Fleet      container | bulk | tanker | ro_ro
  Extractive & Fishing  trawler | longliner | purse_seiner | reefer
  Enforcement & State   coast_guard | naval | ngo
  Support & Special     research | tug | supply | icebreaker
"""

from __future__ import annotations

from typing import List, Protocol, runtime_checkable

from .geo import is_in_protected_area
from .risk_engine import Vessel


@runtime_checkable
class VesselSource(Protocol):
    """Vertrag fuer jede Datenquelle - synthetisch wie real.

    Eine Quelle muss genau diese Methode anbieten. Die Engine (und die API)
    sprechen nur gegen dieses Protokoll, nie gegen eine konkrete Implementierung.
    """

    def get_vessels(self) -> List[Vessel]:
        ...


# Fiktives Schutzgebiet als Bezugspunkt der Demo-Szene (Galapagos-artige Zone).
# Nur fuer die Kartenmitte; die `in_protected_area`-Flags sind hier vorgegeben.
PROTECTED_AREA_CENTER = {"lat": -0.5, "lon": -90.5}


_SAMPLE_VESSELS: List[Vessel] = [
    # ==========================================================================
    # EXTRACTIVE & FISHING FLEET  (Diamond marker)
    # High-risk monitoring zone — primary conservation focus.
    # ==========================================================================

    # High risk: trawler with long AIS gap and loitering inside protected zone
    Vessel(
        mmsi="412330001",
        name="Hai Feng 09",
        lat=-0.42,
        lon=-90.61,
        speed_knots=3.2,
        ais_gap_hours=14,
        flag="CHN",
        loitering_hours=8,
        vessel_type="trawler",
    ),
    # High risk: longliner with AIS gap and loitering
    Vessel(
        mmsi="725000777",
        name="Estrella del Sur",
        lat=-0.55,
        lon=-90.48,
        speed_knots=2.6,
        ais_gap_hours=5,
        flag="ECU",
        loitering_hours=7,
        vessel_type="longliner",
    ),
    # Mid risk: purse seiner with significant AIS gap
    Vessel(
        mmsi="416004321",
        name="Ocean Harvest",
        lat=-0.31,
        lon=-90.20,
        speed_knots=4.4,
        ais_gap_hours=13,
        flag="TWN",
        loitering_hours=2,
        vessel_type="purse_seiner",
    ),
    # Mid risk: trawler loitering near protected area boundary
    Vessel(
        mmsi="538009123",
        name="Northern Star",
        lat=-0.78,
        lon=-90.95,
        speed_knots=3.0,
        ais_gap_hours=0,
        flag="MHL",
        loitering_hours=6,
        vessel_type="trawler",
    ),
    # Low risk: trawler, clean AIS, normal transit speed
    Vessel(
        mmsi="477995123",
        name="Blue Horizon",
        lat=-0.62,
        lon=-90.40,
        speed_knots=2.9,
        ais_gap_hours=0,
        flag="HKG",
        loitering_hours=0,
        vessel_type="trawler",
    ),
    # Low risk: factory reefer ship in transit
    Vessel(
        mmsi="305887010",
        name="Pelagic Dawn",
        lat=-1.05,
        lon=-91.10,
        speed_knots=11.0,
        ais_gap_hours=3,
        flag="ATG",
        loitering_hours=1,
        vessel_type="reefer",
    ),

    # ==========================================================================
    # COMMERCIAL FLEET  (Rectangle marker)
    # Highly regulated backbone of global trade — almost always AIS-clean.
    # ==========================================================================

    # Tanker in clear transit
    Vessel(
        mmsi="636017888",
        name="Atlantic Trader",
        lat=-0.10,
        lon=-89.90,
        speed_knots=13.5,
        ais_gap_hours=0,
        flag="LBR",
        loitering_hours=0,
        vessel_type="tanker",
    ),
    # Container ship with a brief unexplained AIS gap near the zone
    Vessel(
        mmsi="566314200",
        name="Pacific Merchant",
        lat=-0.78,
        lon=-90.15,
        speed_knots=9.5,
        ais_gap_hours=6,
        flag="SGP",
        loitering_hours=2,
        vessel_type="container",
    ),
    # Bulk carrier, clean transit
    Vessel(
        mmsi="248412000",
        name="Bulk Jupiter",
        lat=-1.20,
        lon=-89.60,
        speed_knots=11.0,
        ais_gap_hours=0,
        flag="MLT",
        loitering_hours=0,
        vessel_type="bulk",
    ),
    # Ro-Ro carrier, clean high-speed transit
    Vessel(
        mmsi="538070124",
        name="Pacific Wings",
        lat=0.20,
        lon=-89.40,
        speed_knots=14.5,
        ais_gap_hours=0,
        flag="MHL",
        loitering_hours=0,
        vessel_type="ro_ro",
    ),

    # ==========================================================================
    # ENFORCEMENT & STATE FLEET  (Triangle marker)
    # Defenders — maintain security and enforce environmental law.
    # ==========================================================================

    # Coast guard patrol near protected area
    Vessel(
        mmsi="735008001",
        name="EC-Patrol 01",
        lat=-0.35,
        lon=-90.70,
        speed_knots=8.2,
        ais_gap_hours=0,
        flag="ECU",
        loitering_hours=0,
        vessel_type="coast_guard",
        sanctions_check=False,
    ),
    # Naval warship on exercise
    Vessel(
        mmsi="220000001",
        name="HDMS Triton",
        lat=0.10,
        lon=-90.20,
        speed_knots=15.0,
        ais_gap_hours=0,
        flag="DNK",
        loitering_hours=0,
        vessel_type="naval",
        sanctions_check=False,
    ),
    # NGO direct-action vessel (Sea Shepherd-style)
    Vessel(
        mmsi="244860001",
        name="MV Bob Barker",
        lat=-0.50,
        lon=-91.20,
        speed_knots=6.0,
        ais_gap_hours=0,
        flag="NLD",
        loitering_hours=0,
        vessel_type="ngo",
        sanctions_check=False,
    ),

    # ==========================================================================
    # SUPPORT & SPECIAL PURPOSE FLEET  (Capsule marker)
    # Scientific, logistical, and infrastructure roles.
    # ==========================================================================

    # Research vessel on survey
    Vessel(
        mmsi="311000450",
        name="RV Atlantis",
        lat=-1.40,
        lon=-90.80,
        speed_knots=5.5,
        ais_gap_hours=0,
        flag="USA",
        loitering_hours=3,
        vessel_type="research",
        sanctions_check=False,
    ),
    # Tugboat operating in area
    Vessel(
        mmsi="215678900",
        name="Salvage King",
        lat=-0.90,
        lon=-89.80,
        speed_knots=4.0,
        ais_gap_hours=0,
        flag="MLT",
        loitering_hours=0,
        vessel_type="tug",
        sanctions_check=False,
    ),
    # Icebreaker in transit (unusual in equatorial zone — slightly elevated suspicion)
    Vessel(
        mmsi="248521003",
        name="Arctic Pioneer",
        lat=0.30,
        lon=-91.30,
        speed_knots=7.0,
        ais_gap_hours=4,
        flag="NOR",
        loitering_hours=0,
        vessel_type="icebreaker",
        sanctions_check=False,
    ),
]

# Compute in_protected_area from real geometry for every vessel.
for _v in _SAMPLE_VESSELS:
    _v.in_protected_area = is_in_protected_area(_v.lat, _v.lon)


class SyntheticVesselSource:
    """Phase-1-Datenquelle: liefert eine feste synthetische Szene.

    Erfuellt das `VesselSource`-Protokoll. In Phase 2 ersetzt eine reale Quelle
    (gleiche Signatur) diese Klasse - die API bindet sie ueber `get_source()`.
    """

    def get_vessels(self) -> List[Vessel]:
        return list(_SAMPLE_VESSELS)


def get_source() -> VesselSource:
    """Einziger Einstiegspunkt fuer die API.

    Hier - und nur hier - wird in Phase 2 die Datenquelle umgestellt. Der Rest
    des Systems kennt diese Funktion, nicht die konkrete Klasse.
    """
    return SyntheticVesselSource()
