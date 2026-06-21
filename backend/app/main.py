"""Mission Radar - FastAPI Schicht.

Bewusst duenn: Diese Datei haelt keine Fachlogik. Sie holt Schiffe aus der
(austauschbaren) Datenquelle, laesst die (feste) Engine bewerten und
serialisiert das Ergebnis. Saemtliche Bewertungslogik lebt in risk_engine.py.

Start (aus dem Ordner backend/):
    uvicorn app.main:app --reload
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import os

from fastapi import HTTPException, Query

from . import geo, risk_engine
from .risk_engine import RiskReason, TargetAssessment
from .sample_data import PROTECTED_AREA_CENTER, VesselSource, get_source

app = FastAPI(
    title="Mission Radar API",
    description=(
        "Decision-Support fuer die Bekaempfung illegaler Fischerei. "
        "Priorisiert Ziele und liefert IMMER eine Begruendung."
    ),
    version="0.1.0",
)

# CORS fuer das lokale Frontend offen (Phase 1). Fuer einen echten Einsatz
# muessen die erlaubten Origins eingeschraenkt werden.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

# Welche Datenquelle ist Standard? Aus der Umgebung, Default "synthetic", damit
# das Projekt ohne GFW-Token laeuft. Pro Request via ?source= ueberschreibbar.
DEFAULT_DATA_SOURCE = os.environ.get("DATA_SOURCE", "synthetic")

# Die synthetische Quelle wird einmal aufgeloest (guenstig, kein Netzwerk). Die
# GFW-Quelle wird LAZY erst bei Bedarf erzeugt - so stoert ein fehlender Token
# den synthetischen Default nicht.
_synthetic_source = get_source()


Bbox = tuple  # (min_lon, min_lat, max_lon, max_lat)


def parse_region(min_lat, max_lat, min_lon, max_lon, start_date, end_date):
    """Validiert optionale bbox-/Datums-Parameter und baut (bbox, start, end).

    - Werden NICHT alle vier bbox-Werte uebergeben -> bbox=None (Fallback Env).
    - min_lat<max_lat und min_lon<max_lon, sonst HTTP 400.
    - Datum YYYY-MM-DD -> ISO mit Zeit; nur eines gesetzt -> 400.
    """
    coords = [min_lat, max_lat, min_lon, max_lon]
    given = [c for c in coords if c is not None]
    bbox = None
    if given:
        if len(given) != 4:
            raise HTTPException(
                status_code=400,
                detail="bbox unvollstaendig: min_lat,max_lat,min_lon,max_lon "
                       "alle vier oder keinen angeben.",
            )
        if min_lat >= max_lat or min_lon >= max_lon:
            raise HTTPException(
                status_code=400,
                detail="Ungueltige bbox: min_lat<max_lat und min_lon<max_lon "
                       "erforderlich.",
            )
        # gfw_vessels erwartet (min_lon, min_lat, max_lon, max_lat)
        bbox = (min_lon, min_lat, max_lon, max_lat)

    start = end = None
    if start_date or end_date:
        if not (start_date and end_date):
            raise HTTPException(
                status_code=400,
                detail="Zeitfenster unvollstaendig: start_date UND end_date "
                       "angeben (YYYY-MM-DD).",
            )
        start = f"{start_date}T00:00:00Z"
        end = f"{end_date}T23:59:59Z"

    return bbox, start, end


def resolve_source(name: str, bbox=None, start=None, end=None) -> VesselSource:
    """Waehlt die Datenquelle anhand des Namens ("synthetic" oder "gfw").

    GFW wird nur hier importiert/instanziiert, damit das System ohne installierte
    GFW-Konfiguration im synthetischen Default voll funktioniert. bbox/Zeitfenster
    werden nur an die GFW-Quelle durchgereicht; synthetic ignoriert sie.
    """
    key = (name or DEFAULT_DATA_SOURCE).lower()
    if key == "synthetic":
        return _synthetic_source
    if key == "gfw":
        # Echte AIS-/Schiffsquelle: Global Fishing Watch API (gfw_vessels.py).
        # Lazy import, damit das System ohne Token im synthetischen Default laeuft.
        from .gfw_vessels import get_gfw_source  # lazy import

        # Schutzgebiets-Pruefung auf die GEWAEHLTE Region setzen (sonst wuerden
        # in_protected_area-Flags ausserhalb der Default-Region falsch sein).
        if bbox is not None:
            geo.set_area(bbox)
        return get_gfw_source(bbox=bbox, start=start, end=end)
    raise HTTPException(
        status_code=400,
        detail=f"Unbekannte Datenquelle '{name}'. Erlaubt: 'synthetic', 'gfw'.",
    )


def _load_vessels(source: str, bbox=None, start=None, end=None):
    """Holt die Schiffe der gewaehlten Quelle und uebersetzt Quellen-Fehler sauber.

    Zu grosse bbox -> HTTP 400 (Client). GFW-Probleme (Token, Netzwerk, HTTP) ->
    HTTP 502 - aber niemals stillschweigend verschluckt.
    """
    vessel_source = resolve_source(source, bbox, start, end)
    try:
        return vessel_source.get_vessels()
    except HTTPException:
        raise
    except Exception as exc:  # z. B. GfwApiError, AreaTooLargeError
        # Area-too-large ist ein Client-Fehler (400), kein Server-/Quellenfehler.
        if type(exc).__name__ == "AreaTooLargeError":
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        raise HTTPException(
            status_code=502,
            detail=f"Datenquelle '{source}' fehlgeschlagen: {exc}",
        ) from exc


# --------------------------------------------------------------------------- #
# Serialisierung
# --------------------------------------------------------------------------- #


def _reason_to_dict(reason: RiskReason) -> Dict[str, Any]:
    return {
        "points": reason.points,
        "label": reason.label,
        "detail": reason.detail,
    }


def _assessment_to_dict(a: TargetAssessment) -> Dict[str, Any]:
    """Ein bewertetes Schiff als JSON inkl. Score, top_reason und reasons."""
    top = a.top_reason
    return {
        "mmsi": a.vessel.mmsi,
        "name": a.vessel.name,
        "lat": a.vessel.lat,
        "lon": a.vessel.lon,
        "speed_knots": a.vessel.speed_knots,
        "in_protected_area": a.vessel.in_protected_area,
        "ais_gap_hours": a.vessel.ais_gap_hours,
        "flag": a.vessel.flag,
        "loitering_hours": a.vessel.loitering_hours,
        "score": a.score,
        "top_reason": _reason_to_dict(top) if top else None,
        "reasons": [_reason_to_dict(r) for r in a.reasons],
    }


# --------------------------------------------------------------------------- #
# Endpunkte
# --------------------------------------------------------------------------- #


@app.get("/")
def health() -> Dict[str, Any]:
    """Health-Check."""
    return {
        "status": "ok",
        "service": "Mission Radar API",
        "version": app.version,
        "rules_loaded": len(risk_engine.RULES),
        "default_data_source": DEFAULT_DATA_SOURCE,
    }


# Gemeinsame Query-Beschreibung fuer beide Endpunkte.
_SOURCE_QUERY = Query(
    default=DEFAULT_DATA_SOURCE,
    description="Datenquelle: 'synthetic' (Default) oder 'gfw'.",
)


@app.get("/api/targets")
def get_targets(
    top_n: int = 5,
    source: str = _SOURCE_QUERY,
    min_lat: Optional[float] = None,
    max_lat: Optional[float] = None,
    min_lon: Optional[float] = None,
    max_lon: Optional[float] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Top-N Ziele mit Begruendung - gerankt nach Score absteigend."""
    bbox, start, end = parse_region(min_lat, max_lat, min_lon, max_lon,
                                    start_date, end_date)
    vessels = _load_vessels(source, bbox, start, end)
    ranked = risk_engine.rank_targets(vessels, top_n=top_n)
    return {
        "source": source,
        "count": len(ranked),
        "targets": [_assessment_to_dict(a) for a in ranked],
    }


@app.get("/api/vessels")
def get_vessels(
    source: str = _SOURCE_QUERY,
    min_lat: Optional[float] = None,
    max_lat: Optional[float] = None,
    min_lon: Optional[float] = None,
    max_lon: Optional[float] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Alle Schiffe mit Score - fuer die Kartendarstellung."""
    bbox, start, end = parse_region(min_lat, max_lat, min_lon, max_lon,
                                    start_date, end_date)
    vessels = _load_vessels(source, bbox, start, end)
    assessments = risk_engine.assess_all(vessels)
    return {
        "source": source,
        "count": len(assessments),
        "protected_area_center": PROTECTED_AREA_CENTER,
        "vessels": [_assessment_to_dict(a) for a in assessments],
    }


@app.get("/api/protected-areas")
def get_protected_areas(
    min_lat: Optional[float] = None,
    max_lat: Optional[float] = None,
    min_lon: Optional[float] = None,
    max_lon: Optional[float] = None,
) -> Dict[str, Any]:
    """Schutzgebiets-Polygone als GeoJSON - fuer den Karten-Layer.

    Optional bbox: laedt WDPA-Polygone fuer die gewaehlte Region (passend zur
    Schiffs-Abfrage). Ohne bbox bleibt die Env-Default-Region.
    Fehler der Geo-Quelle werden als HTTP 502 gemeldet, nie still verschluckt.
    """
    bbox, _, _ = parse_region(min_lat, max_lat, min_lon, max_lon, None, None)
    if bbox is not None:
        geo.set_area(bbox)
    try:
        return geo.get_protected_areas_geojson()
    except Exception as exc:  # z. B. GfwDataApiError bei Quelle "gfw"
        raise HTTPException(
            status_code=502,
            detail=f"Schutzgebiets-Daten konnten nicht geladen werden: {exc}",
        ) from exc
