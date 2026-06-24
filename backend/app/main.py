"""Mission Radar - FastAPI Schicht.

Bewusst duenn: Diese Datei haelt keine Fachlogik. Sie holt Schiffe aus der
(austauschbaren) Datenquelle, laesst die (feste) Engine bewerten und
serialisiert das Ergebnis. Saemtliche Bewertungslogik lebt in risk_engine.py.

Start (aus dem Ordner backend/):
    uvicorn app.main:app --reload
"""

from __future__ import annotations

import asyncio
import json
from time import monotonic
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

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

_VESSEL_CACHE_TTL_SECONDS = float(os.environ.get("VESSEL_CACHE_TTL_SECONDS", "5"))
_vessel_cache: dict[str, tuple[float, list]] = {}


def _warmup_static_sources() -> None:
    """Laedt die statischen Quellen-Caches EINMAL beim Start in den Speicher.

    Danach sind die Regel-Lookups (IUU/Sanktionen/PSC/EEZ) reine In-Memory-
    Zugriffe - kein Datei-/Netzwerk-Zugriff im Request-Pfad. Fehlt eine Quelle,
    bleibt sie leer (Regel feuert nicht) - synthetic laeuft trotzdem.
    """
    from .sources import eez, iuu_list, opensanctions, port_control
    for mod in (iuu_list, opensanctions, port_control, eez):
        try:
            mod.warmup()
        except Exception as exc:  # noqa: BLE001
            import logging
            logging.getLogger("mission_radar.main").warning(
                "Warmup der Quelle %s fehlgeschlagen: %s", mod.SOURCE, exc)


_warmup_static_sources()


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


def _cache_key(source: str, bbox=None, start=None, end=None) -> str:
    return f"{source}|{bbox}|{start}|{end}"


def _load_vessels(source: str, bbox=None, start=None, end=None):
    """Holt die Schiffe der gewaehlten Quelle und uebersetzt Quellen-Fehler sauber.

    Zu grosse bbox -> HTTP 400 (Client). GFW-Probleme (Token, Netzwerk, HTTP) ->
    HTTP 502 - aber niemals stillschweigend verschluckt.
    """
    key = _cache_key(source, bbox, start, end)
    now = monotonic()
    cached = _vessel_cache.get(key)
    if cached is not None:
        cached_at, cached_vessels = cached
        if now - cached_at < _VESSEL_CACHE_TTL_SECONDS:
            return list(cached_vessels)

    vessel_source = resolve_source(source, bbox, start, end)
    try:
        vessels = vessel_source.get_vessels()
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

    _vessel_cache[key] = (now, list(vessels))
    return list(vessels)


# --------------------------------------------------------------------------- #
# Serialisierung
# --------------------------------------------------------------------------- #


def _reason_to_dict(reason: RiskReason) -> Dict[str, Any]:
    return {
        "points": reason.points,
        "label": reason.label,
        "detail": reason.detail,
        "evidence_type": reason.evidence_type,
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
        "vessel_type": getattr(a.vessel, "vessel_type", "unknown"),
        "score": a.score,
        "risk_score": a.score,
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

    Fallback: liefert die GFW-Data-API keine Polygone (Ausfall/leer), wird die
    lokale Platzhalter-GeoJSON zurueckgegeben (auf die bbox gefiltert). So bleibt
    der Karten-Layer auch bei API-Ausfall nutzbar (Option A als Sicherheitsnetz).
    """
    bbox, _, _ = parse_region(min_lat, max_lat, min_lon, max_lon, None, None)
    if bbox is not None:
        geo.set_area(bbox)

    fc = geo.get_protected_areas_geojson()   # gfw oder local, graceful
    source = "gfw" if geo._source() == "gfw" else "local"
    if not fc.get("features"):
        # GFW lieferte nichts (Ausfall ODER Region ohne MPA) -> lokaler Fallback.
        fallback = geo.local_protected_areas(bbox)
        if fallback.get("features"):
            fc = fallback
            source = "local-fallback"
    return {
        "type": fc.get("type", "FeatureCollection"),
        "features": fc.get("features", []),
        "source": source,
        "count": len(fc.get("features", [])),
    }


_STREAM_POLL_SECONDS = float(os.environ.get("VESSEL_STREAM_POLL_SECONDS", "3"))


@app.get("/api/vessels/stream")
async def stream_vessels(
    request: Request,
    source: str = _SOURCE_QUERY,
    min_lat: Optional[float] = None,
    max_lat: Optional[float] = None,
    min_lon: Optional[float] = None,
    max_lon: Optional[float] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
):
    """SSE-Stream fuer Live-Schiffspositionen.

    Haelt eine langlebige HTTP-Verbindung offen und pushed neue Vessel-Daten
    nur wenn sich die Datenlage gegenueber dem letzten Tick geaendert hat.
    Der Client muss keine Polling-Logik implementieren; EventSource baut die
    Verbindung bei Verbindungsabbruch automatisch neu auf.
    """
    bbox, start, end = parse_region(min_lat, max_lat, min_lon, max_lon,
                                    start_date, end_date)
    src = (source or DEFAULT_DATA_SOURCE).lower()

    async def generate():
        last_fp: tuple = ()
        while True:
            if await request.is_disconnected():
                break
            try:
                vessels = _load_vessels(src, bbox, start, end)
                assessments = risk_engine.assess_all(vessels)
                fp = tuple((a.vessel.mmsi, round(a.score, 1)) for a in assessments)
                if fp != last_fp:
                    last_fp = fp
                    payload = {
                        "source": src,
                        "count": len(assessments),
                        "vessels": [_assessment_to_dict(a) for a in assessments],
                    }
                    yield {"data": json.dumps(payload)}
            except HTTPException as exc:
                yield {"event": "error", "data": exc.detail}
                break
            except Exception as exc:
                yield {"event": "error", "data": str(exc)}
                break
            await asyncio.sleep(_STREAM_POLL_SECONDS)

    return EventSourceResponse(generate())
