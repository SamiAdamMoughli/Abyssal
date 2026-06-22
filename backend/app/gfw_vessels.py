"""Mission Radar - Global FISHING Watch API (echte AIS-/Schiffsdatenquelle).

Dies ist die EIGENTLICHE Schiffsdatenquelle fuer den Risk Score. Sie spricht die
Global *Fishing* Watch API v3 an (Vessels, Events, 4Wings) - NICHT die Global
*Forest* Watch Data API (das ist gfw_data_api.py, nur Schutzgebiete).

Verifiziert gegen die LIVE-API (echter Token, Galapagos-Daten):
  https://globalfishingwatch.org/our-apis/documentation
  - Base URL: https://gateway.api.globalfishingwatch.org/v3
  - Auth:     Authorization: Bearer <GFW_API_TOKEN>
  - Events:   POST /events  - Filter (datasets, startDate, endDate, geometry) im
              JSON-BODY; limit/offset als QUERY-Parameter (sonst HTTP 422!).
              Erfolg = HTTP 201; "entries"-Liste, "total", "nextOffset".
              Event-Objekt: start, end, id, type (klein: fishing/gap/loitering),
                            position{lat,lon}, vessel{id,name,ssvid,flag}, regions
  - Datasets: gap und loitering liegen in EIGENEN Datasets (s. EVENT_DATASETS);
              mehrere Datasets pro Call sind erlaubt.
  - Positionen: kein roher Track-Endpunkt; Position kommt aus den Events selbst.

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

# Datasets - alle gegen die Live-API verifiziert. gap und loitering liegen in
# EIGENEN Datasets (nicht im fishing-events-Dataset); fuer ais_gap_hours und
# loitering_hours muessen daher alle drei abgefragt werden. Mehrere Datasets in
# EINEM /events-Call sind moeglich (verifiziert).
EVENT_DATASETS: List[str] = os.environ.get(
    "GFW_EVENT_DATASETS",
    "public-global-fishing-events:latest,"
    "public-global-gaps-events:latest,"
    "public-global-loitering-events:latest",
).split(",")
VESSEL_IDENTITY_DATASET = os.environ.get(
    "GFW_VESSEL_DATASET", "public-global-vessel-identity:latest"
)

# Event-Typ-Bezeichner - verifiziert gegen die Live-API: type ist KLEIN
# geschrieben ("gap", "loitering", "fishing"). Vergleich erfolgt case-insensitiv.
EVENT_TYPE_GAP = os.environ.get("GFW_EVENT_TYPE_GAP", "gap").lower()
EVENT_TYPE_LOITERING = os.environ.get("GFW_EVENT_TYPE_LOITERING", "loitering").lower()

# bounding box: (min_lon, min_lat, max_lon, max_lat)
BBox = Tuple[float, float, float, float]


class GfwApiError(RuntimeError):
    """Wird bei jedem Problem mit der GFW API geworfen - keine stillen Fehler."""


class AreaTooLargeError(ValueError):
    """bbox ueberschreitet das erlaubte Maximum (Rate-Limit-/Last-Schutz)."""


# Maximale Kantenlaenge einer Abfrage-bbox in Grad. Groessere Gebiete wuerden zu
# viele Events liefern (Timeout/Rate-Limit) - bewusst begrenzt, keine Weltabfrage.
MAX_BBOX_DEGREES = 20.0


def validate_bbox_size(bbox: "BBox") -> None:
    """Wirft AreaTooLargeError, wenn die bbox groesser als MAX_BBOX_DEGREES ist."""
    min_lon, min_lat, max_lon, max_lat = bbox
    if (max_lon - min_lon) > MAX_BBOX_DEGREES or (max_lat - min_lat) > MAX_BBOX_DEGREES:
        raise AreaTooLargeError(
            "Area too large - zoom in and try again "
            f"(max {MAX_BBOX_DEGREES:.0f}° x {MAX_BBOX_DEGREES:.0f}°)."
        )


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


def fetch_events(bbox: BBox, start: str, end: str,
                 page_size: int = 100, max_events: int = 1500) -> List[Dict[str, Any]]:
    """Holt Events im Gebiet + Zeitfenster ueber POST /events (paginiert).

    Verifiziert gegen die Live-API:
      - Filter (datasets, startDate, endDate, geometry) gehen in den JSON-BODY.
      - limit/offset sind QUERY-Parameter (nicht im Body, sonst HTTP 422).
      - Erfolg ist HTTP 201; Ergebnisliste steht unter "entries", Gesamtzahl
        unter "total", die naechste Seite unter "nextOffset".
    """
    body: Dict[str, Any] = {
        "datasets": EVENT_DATASETS,
        "startDate": start,
        "endDate": end,
        "geometry": _bbox_to_geojson_polygon(bbox),
    }

    all_entries: List[Dict[str, Any]] = []
    offset = 0
    while len(all_entries) < max_events:
        payload = _request(
            "POST", EVENTS_ENDPOINT,
            params={"limit": page_size, "offset": offset},
            json_body=body,
        )
        entries = payload.get("entries")
        if not isinstance(entries, list):
            raise GfwApiError(
                "Unerwartetes Events-Antwortformat: keine 'entries'-Liste gefunden."
            )
        all_entries.extend(entries)

        total = payload.get("total", len(all_entries))
        next_offset = payload.get("nextOffset")
        if not entries or next_offset is None or len(all_entries) >= total:
            break
        offset = next_offset

    return all_entries


# --------------------------------------------------------------------------- #
# Mapping: GFW-Events -> Vessel    <<< verifiziert gegen Live-API >>>
# --------------------------------------------------------------------------- #


# Obergrenze fuer eine einzelne Event-Dauer. Ein offenes Event ohne "end" wuerde
# sonst (Dauer bis "jetzt") bei historischen Abfragen absurde Werte liefern
# (mehrere Jahre). Fuer das Risiko ist alles oberhalb der hoechsten Regelschwelle
# ohnehin gleichwertig; 168h (1 Woche) deckelt das plausibel.
MAX_EVENT_DURATION_HOURS = 168.0


def _event_duration_hours(event: Dict[str, Any]) -> float:
    """Dauer eines Events in Stunden aus start/end (ISO-8601). 0.0, wenn unklar.

    Auf MAX_EVENT_DURATION_HOURS gedeckelt, damit offene Events (ohne "end")
    keine unrealistischen Dauern erzeugen.
    """
    try:
        start = event.get("start")
        end = event.get("end")
        if not start:
            return 0.0
        t0 = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        # Laeuft das Event noch (kein end), Dauer bis jetzt rechnen - konservativ.
        t1 = (datetime.fromisoformat(str(end).replace("Z", "+00:00"))
              if end else datetime.now(timezone.utc))
        hours = (t1 - t0).total_seconds() / 3600.0
        return max(min(hours, MAX_EVENT_DURATION_HOURS), 0.0)
    except (ValueError, TypeError):
        return 0.0


def _duration_from_field(value: Any, event: Dict[str, Any]) -> float:
    """Dauer in Stunden aus einem expliziten GFW-Feld (kann str oder Zahl sein).

    Verifiziert gegen die Live-API: gap-Events fuehren die Dauer in
    event["gap"]["durationHours"], loitering-Events in
    event["loitering"]["totalTimeHours"] - beides genauer als start/end.
    Fehlt das Feld -> Fallback auf start/end. In jedem Fall auf
    MAX_EVENT_DURATION_HOURS gedeckelt (lange Aggregat-Werte vermeiden).
    """
    if value is None:
        return _event_duration_hours(event)
    try:
        hours = float(value)
    except (ValueError, TypeError):
        return _event_duration_hours(event)
    return max(min(hours, MAX_EVENT_DURATION_HOURS), 0.0)


def _normalize_vessel_type(value: Any) -> str:
    """Maps raw GFW type strings to one of the 13 subcategory slugs.

    Category structure:
      Commercial Fleet   → container, bulk, tanker, ro_ro
      Extractive/Fishing → trawler, longliner, purse_seiner, reefer
      Enforcement/State  → coast_guard, naval, ngo
      Support/Special    → research, tug, supply, icebreaker
    """
    if not value:
        return "unknown"
    text = str(value).strip().lower()
    if not text:
        return "unknown"

    # Extractive & Fishing Fleet — most specific first
    if any(k in text for k in ["trawl", "bottom trawl", "midwater trawl"]):
        return "trawler"
    if any(k in text for k in ["longlin", "long line", "long-line"]):
        return "longliner"
    if any(k in text for k in ["purse sein", "seiner"]):
        return "purse_seiner"
    if any(k in text for k in ["reefer", "refrigerat", "factory ship", "processing ship", "cold storage"]):
        return "reefer"
    if any(k in text for k in ["fishing", "fisher", "fish vessel", "whaling", "whale catcher"]):
        return "trawler"  # generic fishing → trawler as default subcategory

    # Commercial Fleet
    if any(k in text for k in ["container", "box ship", "containership"]):
        return "container"
    if any(k in text for k in ["bulk carrier", "bulker", "ore carrier", "grain carrier", "bulk"]):
        return "bulk"
    if any(k in text for k in ["tanker", "oil tanker", "gas tanker", "chemical tanker",
                                 "product tanker", "vlcc", "ulcc", "aframax", "suezmax"]):
        return "tanker"
    if any(k in text for k in ["ro-ro", "roro", "roll-on", "roll on", "car carrier",
                                 "vehicle carrier", "pctc"]):
        return "ro_ro"
    if any(k in text for k in ["cargo", "general cargo", "break bulk", "freighter"]):
        return "bulk"  # generic cargo → bulk

    # Enforcement & State Fleet
    if any(k in text for k in ["coast guard", "coastguard", "patrol boat", "patrol vessel",
                                 "patrol ship", "border"]):
        return "coast_guard"
    if any(k in text for k in ["naval", "warship", "frigate", "destroyer", "corvette",
                                 "navy", "military vessel"]):
        return "naval"
    if any(k in text for k in ["ngo", "conservation", "sea shepherd", "greenpeace",
                                 "environmental vessel"]):
        return "ngo"
    if any(k in text for k in ["patrol", "enforcement", "surveillance", "military"]):
        return "coast_guard"  # generic patrol → coast_guard

    # Support & Special Purpose Fleet
    if any(k in text for k in ["research", "survey vessel", "science", "oceanograph", "scientific"]):
        return "research"
    if any(k in text for k in ["tug", "tugboat", "towing vessel", "salvage tug"]):
        return "tug"
    if any(k in text for k in ["supply", "offshore supply", "platform supply", "anchor handling"]):
        return "supply"
    if any(k in text for k in ["icebreak", "ice break", "polar"]):
        return "icebreaker"
    if any(k in text for k in ["cable", "dredge", "buoy tender", "crane vessel", "pipe lay"]):
        return "supply"  # misc special purpose → supply

    return "unknown"


def _guess_vessel_type(vessel: Dict[str, Any], name: str, speed_knots: float,
                       event_type: str) -> str:
    raw_type = vessel.get("type") or vessel.get("vessel_type") or vessel.get("ship_type")
    candidate = _normalize_vessel_type(raw_type)
    if candidate != "unknown":
        return candidate

    if name:
        key = name.lower()
        if any(token in key for token in ["tanker", "oil", "gas", "chemical", "vlcc"]):
            return "tanker"
        if any(token in key for token in ["container", "containership"]):
            return "container"
        if any(token in key for token in ["bulk", "carrier", "ore", "grain"]):
            return "bulk"
        if any(token in key for token in ["reefer", "cold", "frozen", "factory"]):
            return "reefer"
        if any(token in key for token in ["trawler", "seiner", "longliner"]):
            return candidate  # normalized already handled this
        if any(token in key for token in ["fish", "fisher"]):
            return "trawler"
        if any(token in key for token in ["research", "survey", "science"]):
            return "research"
        if any(token in key for token in ["coast guard", "patrol", "naval"]):
            return "coast_guard"
    if event_type == "fishing":
        return "trawler"
    if 0 < speed_knots < 7.5:
        return "trawler"
    return "unknown"


def _vessels_from_events(events: List[Dict[str, Any]]) -> List[Vessel]:
    """Gruppiert Events pro Schiff und baut daraus Vessel-Objekte.

    Feldpfade verifiziert gegen die Live-API (rohe Event-JSON):
      - event["vessel"]: {id, name, ssvid, flag, type}  (ssvid = AIS-MMSI)
      - event["position"]: {lat, lon}
      - event["type"]: "fishing" | "gap" | "loitering" (klein)
      - event["fishing"]["averageSpeedKnots"]   (nur fishing-Events)
      - event["gap"]["durationHours"]           (nur gap-Events)
      - event["loitering"]["totalTimeHours"]    (nur loitering-Events)
    Fehlende Felder -> konservativer Default (kein erfundenes Risiko).
    """
    by_vessel: Dict[str, Dict[str, Any]] = {}

    for ev in events:
        vessel = ev.get("vessel") or {}
        # AIS-MMSI steht im Feld "ssvid"; "id" ist die interne GFW-Vessel-ID.
        mmsi = str(vessel.get("ssvid") or vessel.get("id") or "").strip()
        if not mmsi:
            continue  # ohne Identitaet kein sinnvolles Ziel

        # type ist klein geschrieben; Konstanten sind ebenfalls klein.
        ev_type = str(ev.get("type", "")).lower()

        slot = by_vessel.setdefault(mmsi, {
            "name": vessel.get("name") or "UNBEKANNT",
            "flag": vessel.get("flag") or "UNK",
            "lat": None, "lon": None, "latest_ts": None,
            "speed_knots": 0.0,        # konservativer Default, s. u.
            "ais_gap_hours": 0.0,
            "loitering_hours": 0.0,
            "vessel": vessel,
            "event_type": ev_type,
        })
        slot["vessel"] = vessel
        slot["event_type"] = ev_type

        # --- Position: juengstes Event gewinnt -------------------------------
        pos = ev.get("position") or {}
        lat, lon = pos.get("lat"), pos.get("lon")
        ts = ev.get("end") or ev.get("start")
        if lat is not None and lon is not None and ts is not None:
            if slot["latest_ts"] is None or str(ts) > str(slot["latest_ts"]):
                slot["latest_ts"] = ts
                slot["lat"] = float(lat)
                slot["lon"] = float(lon)

        # type ist klein geschrieben; Konstanten sind ebenfalls klein.
        ev_type = str(ev.get("type", "")).lower()

        # --- Geschwindigkeit: aus fishing-Event (averageSpeedKnots) ----------
        fishing = ev.get("fishing") or {}
        spd = fishing.get("averageSpeedKnots")
        if spd is not None:
            slot["speed_knots"] = float(spd)

        # --- AIS-Luecke (gap) -> ais_gap_hours -------------------------------
        # Echtes Feld: event["gap"]["durationHours"] (verifiziert). Laengste
        # gap-Dauer im Fenster verwenden (konservativ-relevanteste Luecke).
        if ev_type == EVENT_TYPE_GAP:
            gap = ev.get("gap") or {}
            dur = _duration_from_field(gap.get("durationHours"), ev)
            slot["ais_gap_hours"] = max(slot["ais_gap_hours"], dur)

        # --- Verweilen (loitering) -> loitering_hours ------------------------
        # Echtes Feld: event["loitering"]["totalTimeHours"] (verifiziert).
        if ev_type == EVENT_TYPE_LOITERING:
            loit = ev.get("loitering") or {}
            dur = _duration_from_field(loit.get("totalTimeHours"), ev)
            slot["loitering_hours"] = max(slot["loitering_hours"], dur)

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
            vessel_type=_guess_vessel_type(
                s.get("vessel", {}), s["name"], s["speed_knots"], s.get("event_type", "")
            ),
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
    ab; speed_knots aus fishing-Events, sonst konservativ 0.0. Wirft
    AreaTooLargeError bei zu grosser bbox, sonst GfwApiError bei API-Problemen.
    """
    validate_bbox_size(bbox)  # Rate-Limit-Schutz: max 20° x 20°
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


def get_gfw_source(bbox: BBox | None = None,
                   start: str | None = None, end: str | None = None) -> GfwVesselSource:
    """Einstiegspunkt fuer main.py - analog zu sample_data.get_source().

    bbox/start/end optional: werden sie nicht uebergeben, greifen die
    Env-Defaults (rueckwaertskompatibel).
    """
    return GfwVesselSource(bbox=bbox, start=start, end=end)
