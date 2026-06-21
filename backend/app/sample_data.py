"""Mission Radar - Datenquelle (Phase 1: synthetisch).

Dies ist die AUSTAUSCHBARE Schicht. Die Engine haengt nicht von dieser Datei ab,
sondern nur von der `Vessel`-Datenklasse. In Phase 2 wird diese Datei durch einen
echten Adapter ersetzt (z. B. Global Fishing Watch API), der ebenfalls
`List[Vessel]` liefert - die Engine bleibt unangetastet.

Der explizite `VesselSource`-Protokoll-Vertrag dokumentiert, was eine Datenquelle
erfuellen muss.
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


# Bewusst gemischte Szene: klare Verdachtsfaelle, Grenzfaelle und sauberer Transit.
# So laesst sich pruefen, ob das Ranking sinnvoll trennt statt alles hochzustufen.
#
# Hinweis: `in_protected_area` wird hier NICHT mehr gesetzt. Es wird weiter unten
# pro Schiff aus (lat, lon) ueber geo.is_in_protected_area() berechnet.
_SAMPLE_VESSELS: List[Vessel] = [
    # --- Klare Verdachtsfaelle (sollten oben landen) ---------------------- #
    Vessel(
        mmsi="412330001",
        name="Hai Feng 09",
        lat=-0.42,
        lon=-90.61,
        speed_knots=3.2,            # Fischerei-Tempo
        ais_gap_hours=14,           # lange AIS-Luecke
        flag="CHN",
        loitering_hours=8,          # verweilt lange
    ),
    Vessel(
        mmsi="725000777",
        name="Estrella del Sur",
        lat=-0.55,
        lon=-90.48,
        speed_knots=2.6,            # Fischerei-Tempo
        ais_gap_hours=5,            # moderate AIS-Luecke
        flag="ECU",
        loitering_hours=7,          # verweilt
    ),
    # --- Grenzfaelle (mittlerer Score) ----------------------------------- #
    Vessel(
        mmsi="416004321",
        name="Ocean Harvest",
        lat=-0.31,
        lon=-90.20,
        speed_knots=4.4,            # Fischerei-Tempo
        ais_gap_hours=13,           # lange AIS-Luecke
        flag="TWN",
        loitering_hours=2,
    ),
    Vessel(
        mmsi="538009123",
        name="Northern Star",
        lat=-0.78,
        lon=-90.95,
        speed_knots=3.0,            # Fischerei-Tempo
        ais_gap_hours=0,
        flag="MHL",
        loitering_hours=6,          # verweilt
    ),
    # --- Unauffaellig (Transit, sollte unten/ausgeschlossen sein) -------- #
    Vessel(
        mmsi="636017888",
        name="Atlantic Trader",
        lat=-0.10,
        lon=-89.90,
        speed_knots=13.5,           # klarer Transit
        ais_gap_hours=0,
        flag="LBR",
        loitering_hours=0,
    ),
    Vessel(
        mmsi="305887010",
        name="Pelagic Dawn",
        lat=-1.05,
        lon=-91.10,
        speed_knots=11.0,           # Transit
        ais_gap_hours=3,            # unterhalb der Schwelle
        flag="ATG",
        loitering_hours=1,
    ),
    Vessel(
        mmsi="477995123",
        name="Blue Horizon",
        lat=-0.62,
        lon=-90.40,
        speed_knots=2.9,            # Fischerei-Tempo, aber sonst sauber
        ais_gap_hours=0,
        flag="HKG",
        loitering_hours=0,
    ),
]

# Das Schutzgebiets-Flag echt berechnen: einmal beim Import, aus den Koordinaten.
# Die Engine bekommt damit ein fertiges Vessel-Objekt und kennt keine Geometrie.
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
