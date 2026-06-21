"""Mission Radar - Global FISHING Watch API (echte AIS-/Schiffsdatenquelle).

Dies ist die EIGENTLICHE Schiffsdatenquelle fuer den Risk Score. Sie spricht die
Global *Fishing* Watch API v3 an (Vessels, Events, 4Wings) - NICHT die Global
*Forest* Watch Data API (das ist gfw_data_api.py, nur Schutzgebiete).

Verifiziert gegen die offizielle Doku (Stand der Recherche):
  https://globalfishingwatch.org/our-apis/documentation
  - Base URL: https://gateway.api.globalfishingwatch.org/v3
  - Auth:     Authorization: Bearer <GFW_API_TOKEN>
  - Vessels:  GET /vessels/search   (datasets[0]=public-global-vessel-identity:latest)
  - Events:   GET/POST /events      (datasets[0]=public-global-fishing-events:latest)
              Event-Objekt: start, end, id, type, position{lat,lon},
                            vessel{id, name, ssvid}, regions{...}
  - Positionen: kein roher Track-Endpunkt; Praesenz/Effort via POST /4wings/report
                (public-global-presence:latest / public-global-fishing-effort:latest)

NICHT eindeutig aus der Doku-Recherche und daher unten KLAR MARKIERT (bitte in der
Doku verifizieren, nicht raten):
  - die exakten dataset-IDs bzw. `type`-Enum-Werte fuer GAP (AIS-off) und LOITERING
  - die genaue POST-Body-Syntax fuer einen Geometrie-/bbox-Filter
  - konkrete Rate-Limit-Zahlen (die Doku nennt Rate-Limit-Header, aber keine Zahlen)

Grundsatz bei fehlenden Feldern: konservativer Default (lieber KEIN Risiko annehmen
als ein falsches). Die Risk Engine bekommt am Ende fertige Vessel-Objekte und weiss
nichts von alldem.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple

import requests
from dotenv import load_dotenv

from .geo import is_in_protected_area
from .risk_engine import Vessel

load_dotenv()

logger = logging.getLogger("mission_radar.gfw_vessels")

# --------------------------------------------------------------------------- #
# Konfiguration (Endpunkte + Datasets als Konstanten - leicht anpassbar)
# --------------------------------------------------------------------------- #

GFW_API_BASE = os.environ.get("GFW_API_BASE", "https://gateway.api.globalfishingwatch.org/v3")
HTTP_TIMEOUT_SECONDS = float(os.environ.get("GFW_HTTP_TIMEOUT", "30"))

# Endpunkt-Pfade (verifiziert).
EVENTS_ENDPOINT = "/events"
VESSELS_SEARCH_ENDPOINT = "/vessels/search"

# Datasets. Das Events-Dataset ist verifiziert. Die TYP-spezifischen Datasets/
# Enum-Werte sind in der Doku zu pruefen - siehe EVENT_TYPE_* unten.
EVENTS_DATASET = os.environ.get(
    "GFW_EVENTS_DATASET", "public-global-fishing-events:latest"
)
VESSEL_IDENTITY_DATASET = os.environ.get(
    "GFW_VESSEL_DATASET", "public-global-vessel-identity:latest"
)

# ========================================================================== #
# >>> an echte GFW-Antwort anpassen: Event-Typ-Bezeichner <<<
# ========================================================================== #
# Die Doku-Auszuege bestaetigen die Event-Typen (fishing, loitering, port visit,
# encounter, "AIS off"/gap), aber NICHT die exakten Enum-Strings, die die API im
# Feld event["type"] bzw. im Filter erwartet. Hier sind die plausiblen Werte als
# Konstanten - in der Doku/Live-Antwort verifizieren und ggf. korrigieren.
EVENT_TYPE_GAP = os.environ.get("GFW_EVENT_TYPE_GAP", "GAP")            # ANPASSEN
EVENT_TYPE_LOITERING = os.environ.get("GFW_EVENT_TYPE_LOITERING", "LOITERING")  # ANPASSEN

# bounding box: (min_lon, min_lat, max_lon, max_lat)
BBox = Tuple[float, float, float, float]


class GfwApiError(RuntimeError):
    """Wird bei jedem Problem mit der GFW API geworfen - keine stillen Fehler."""


# --------------------------------------------------------------------------- #
# Token / Auth
# --------------------------------------------------------------------------- #


def _get_token() -> str:
    token = os.environ.get("GFW_API_TOKEN")
    if not token:
        raise GfwApiError(
            "GFW_API_TOKEN ist nicht gesetzt. Token im GFW-Portal anlegen "
            "(https://globalfishingwatch.org/our-apis/tokens) und als "
            "Umgebungsvariable setzen - siehe .env.example."
        )
    return token


def _auth_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {_get_token()}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


# --------------------------------------------------------------------------- #
# Robuste HTTP-Schicht
# --------------------------------------------------------------------------- #
# Rate-Limits: Die GFW-Doku nennt Rate-Limit-HEADER, aber keine festen Zahlen.
# Daher hier kein Hard-Coding von Limits; bei HTTP 429 wird klar gemeldet, damit
# der Aufrufer drosseln/erneut versuchen kann. Fuer groessere Abfragen empfiehlt
# sich Paginierung (limit/offset) und ein eigener Backoff.


def _request(method: str, path: str, *, params: Dict[str, Any] | None = None,
             json_body: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Fuehrt einen GFW-Request aus und gibt das JSON zurueck.

    Robustheit: Timeout, Netzwerk-/HTTP-Fehler -> klare GfwApiError. 429 (Rate
    Limit) wird gesondert gemeldet. Kein stilles Scheitern.
    """
    url = f"{GFW_API_BASE}{path}"
    try:
        response = requests.request(
            method, url, headers=_auth_headers(), params=params, json=json_body,
            timeout=HTTP_TIMEOUT_SECONDS,
        )
    except requests.exceptions.Timeout as exc:
        raise GfwApiError(
            f"GFW API nach {HTTP_TIMEOUT_SECONDS}s ohne Antwort (Timeout): {url}"
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise GfwApiError(f"GFW API nicht erreichbar: {exc}") from exc

    if response.status_code == 401:
        raise GfwApiError(
            "GFW API meldet 401 Unauthorized - Token fehlt, ist abgelaufen oder "
            "hat keine Berechtigung."
        )
    if response.status_code == 429:
        retry_after = response.headers.get("Retry-After", "unbekannt")
        raise GfwApiError(
            f"GFW API Rate Limit (HTTP 429). Retry-After: {retry_after}. "
            "Abfragen drosseln oder paginieren."
        )
    if not response.ok:
        snippet = response.text[:300]
        raise GfwApiError(f"GFW API antwortete mit HTTP {response.status_code}: {snippet}")

    try:
        return response.json()
    except ValueError as exc:
        raise GfwApiError("GFW API lieferte keine gueltige JSON-Antwort.") from exc


# --------------------------------------------------------------------------- #
# Events holen
# --------------------------------------------------------------------------- #


def _bbox_to_geojson_polygon(bbox: BBox) -> Dict[str, Any]:
    """Wandelt eine bbox in ein GeoJSON-Polygon (Reihenfolge lon, lat)."""
    min_lon, min_lat, max_lon, max_lat = bbox
    return {
        "type": "Polygon",
        "coordinates": [[
            [min_lon, min_lat],
            [max_lon, min_lat],
            [max_lon, max_lat],
            [min_lon, max_lat],
            [min_lon, min_lat],
        ]],
    }


def fetch_events(bbox: BBox, start: str, end: str, limit: int = 1000) -> List[Dict[str, Any]]:
    """Holt Events im Gebiet + Zeitfenster ueber POST /events.

    ====================================================================== #
    >>> an echte GFW-Antwort anpassen: POST-Body fuer den Geometrie-Filter <<<
    ====================================================================== #
    Die Doku bestaetigt POST /events und einen Geometrie-Filter, aber die genaue
    Body-Struktur (Schluesselname der Geometrie, Datums-/Dataset-Felder) ist zu
    verifizieren. Die folgenden Schluessel sind eine plausible Annahme.
    """
    body: Dict[str, Any] = {
        "datasets": [EVENTS_DATASET],          # verifiziert
        "startDate": start,                    # ANPASSEN (Feldname/Format pruefen)
        "endDate": end,                        # ANPASSEN
        "geometry": _bbox_to_geojson_polygon(bbox),  # ANPASSEN (Schluesselname pruefen)
        "limit": limit,
    }
    payload = _request("POST", EVENTS_ENDPOINT, json_body=body)

    # Ergebnis-Liste: die GFW-Antwort kapselt Eintraege ueblicherweise unter
    # "entries". ANPASSEN, falls die Antwort anders strukturiert ist.
    entries = payload.get("entries")
    if entries is None:
        entries = payload.get("data", [])     # Fallback
    if not isinstance(entries, list):
        raise GfwApiError(
            "Unerwartetes Events-Antwortformat: keine Liste gefunden "
            "(Schluessel in fetch_events() pruefen)."
        )
    return entries


# --------------------------------------------------------------------------- #
# Mapping: GFW-Events -> Vessel    <<< hier an echte GFW-Antwort anpassen >>>
# --------------------------------------------------------------------------- #


def _event_duration_hours(event: Dict[str, Any]) -> float:
    """Dauer eines Events in Stunden aus start/end (ISO-8601). 0.0, wenn unklar."""
    try:
        start = event.get("start")
        end = event.get("end")
        if not start:
            return 0.0
        t0 = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        # Laeuft das Event noch (kein end), Dauer bis jetzt rechnen - konservativ.
        t1 = (datetime.fromisoformat(str(end).replace("Z", "+00:00"))
              if end else datetime.now(timezone.utc))
        return max((t1 - t0).total_seconds() / 3600.0, 0.0)
    except (ValueError, TypeError):
        return 0.0


def _vessels_from_events(events: List[Dict[str, Any]]) -> List[Vessel]:
    """Gruppiert Events pro Schiff und baut daraus Vessel-Objekte.

    ====================================================================== #
    >>> HIER an echte GFW-Antwort anpassen <<<
    ====================================================================== #
    Die Feldpfade unten (event["vessel"]["ssvid"], event["position"]["lat"], ...)
    folgen den Doku-Beispielen, sind aber an der echten Antwort zu verifizieren.
    Fehlende Felder -> konservativer Default (kein erfundenes Risiko).
    """
    by_vessel: Dict[str, Dict[str, Any]] = {}

    for ev in events:
        vessel = ev.get("vessel") or {}
        # mmsi: GFW nennt die AIS-ID oft "ssvid". ANPASSEN, falls anders benannt.
        mmsi = str(vessel.get("ssvid") or vessel.get("id") or "").strip()
        if not mmsi:
            continue  # ohne Identitaet kein sinnvolles Ziel

        slot = by_vessel.setdefault(mmsi, {
            "name": vessel.get("name") or "UNBEKANNT",     # ANPASSEN
            "flag": vessel.get("flag") or "UNK",           # ANPASSEN (evtl. nicht im Event)
            "lat": None, "lon": None, "latest_ts": None,
            "speed_knots": 0.0,        # konservativer Default, s. u.
            "ais_gap_hours": 0.0,
            "loitering_hours": 0.0,
        })

        # --- Position: juengstes Event gewinnt -------------------------------
        pos = ev.get("position") or {}
        lat, lon = pos.get("lat"), pos.get("lon")
        ts = ev.get("end") or ev.get("start")
        if lat is not None and lon is not None and ts is not None:
            if slot["latest_ts"] is None or str(ts) > str(slot["latest_ts"]):
                slot["latest_ts"] = ts
                slot["lat"] = float(lat)
                slot["lon"] = float(lon)

        ev_type = str(ev.get("type", "")).upper()

        # --- Geschwindigkeit: aus fishing-Event, falls vorhanden -------------
        # ANPASSEN: Pfad/Feldname (Doku-Beispiel: event["fishing"]["averageSpeedKnots"]).
        fishing = ev.get("fishing") or {}
        spd = fishing.get("averageSpeedKnots")
        if spd is not None:
            slot["speed_knots"] = float(spd)

        # --- AIS-Luecke (gap) -> ais_gap_hours -------------------------------
        # Laengste gap-Dauer im Fenster verwenden (konservativ-relevanteste Luecke).
        if ev_type == EVENT_TYPE_GAP:
            slot["ais_gap_hours"] = max(slot["ais_gap_hours"], _event_duration_hours(ev))

        # --- Verweilen (loitering) -> loitering_hours ------------------------
        if ev_type == EVENT_TYPE_LOITERING:
            slot["loitering_hours"] = max(slot["loitering_hours"], _event_duration_hours(ev))

    # In Vessel-Objekte ueberfuehren. Schiffe ohne Position auslassen (ohne
    # lat/lon kein Kartenpunkt und keine Schutzgebiets-Pruefung).
    vessels: List[Vessel] = []
    for mmsi, s in by_vessel.items():
        if s["lat"] is None or s["lon"] is None:
            continue
        vessels.append(Vessel(
            mmsi=mmsi,
            name=s["name"],
            lat=s["lat"],
            lon=s["lon"],
            speed_knots=s["speed_knots"],
            # Schutzgebiet NICHT aus der API - eigene, nachvollziehbare Berechnung:
            in_protected_area=is_in_protected_area(s["lat"], s["lon"]),
            ais_gap_hours=s["ais_gap_hours"],
            flag=s["flag"],
            loitering_hours=s["loitering_hours"],
        ))
    return vessels


# --------------------------------------------------------------------------- #
# Oeffentliche Schnittstelle
# --------------------------------------------------------------------------- #


def fetch_vessels(bbox: BBox, start: str, end: str) -> List[Vessel]:
    """Holt Schiffe inkl. abgeleiteter Risiko-Felder fuer bbox + Zeitfenster.

    bbox  - (min_lon, min_lat, max_lon, max_lat)
    start - ISO-Datum/Zeit (Format gemaess GFW-Doku)
    end   - ISO-Datum/Zeit

    Leitet ais_gap_hours aus GAP-Events und loitering_hours aus LOITERING-Events
    ab; speed_knots aus fishing-Events, sonst konservativ 0.0. Wirft GfwApiError
    bei jedem Problem.
    """
    events = fetch_events(bbox, start, end)
    return _vessels_from_events(events)


# --------------------------------------------------------------------------- #
# Default-bbox / -Zeitfenster + VesselSource
# --------------------------------------------------------------------------- #


def _default_bbox() -> BBox:
    raw = os.environ.get("GFW_BBOX", "-91.5,-1.5,-89.5,0.5")  # grob Galapagos
    try:
        p = [float(x) for x in raw.split(",")]
        return (p[0], p[1], p[2], p[3])
    except (ValueError, IndexError) as exc:
        raise GfwApiError(
            f"GFW_BBOX ungueltig: {raw!r}. Erwartet 'min_lon,min_lat,max_lon,max_lat'."
        ) from exc


def _default_timeframe() -> Tuple[str, str]:
    """Default-Zeitfenster: letzte N Stunden (Default 48h), per Env konfigurierbar.

    GFW_START/GFW_END (ISO) haben Vorrang; sonst GFW_LOOKBACK_HOURS rueckwaerts ab jetzt.
    """
    start = os.environ.get("GFW_START")
    end = os.environ.get("GFW_END")
    if start and end:
        return start, end
    lookback = float(os.environ.get("GFW_LOOKBACK_HOURS", "48"))  # 24-72h sinnvoll
    now = datetime.now(timezone.utc)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return (now - timedelta(hours=lookback)).strftime(fmt), now.strftime(fmt)


class GfwVesselSource:
    """Datenquelle hinter dem VesselSource-Protokoll (get_vessels())."""

    def __init__(self, bbox: BBox | None = None,
                 start: str | None = None, end: str | None = None) -> None:
        self.bbox = bbox or _default_bbox()
        if start and end:
            self.start, self.end = start, end
        else:
            self.start, self.end = _default_timeframe()

    def get_vessels(self) -> List[Vessel]:
        return fetch_vessels(self.bbox, self.start, self.end)


def get_gfw_source() -> GfwVesselSource:
    """Einstiegspunkt fuer main.py - analog zu sample_data.get_source()."""
    return GfwVesselSource()
