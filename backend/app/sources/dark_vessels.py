"""Cache-only "ghost ship" detections from SAR / VIIRS / optical satellites.

==========================================================================
WICHTIG: ASYNC WORKER - NIEMALS IM REQUEST-PFAD.
==========================================================================
SAR-Verarbeitung ist schwer und langsam (Szenen-Download, Detektion, Matching
gegen AIS). Sie gehoert in einen separaten Background-Worker; das Ergebnis
("dark vessels": Radar-Detektionen OHNE passendes AIS) wird gecacht. Der
Request-Pfad liest - wie bei den statischen Quellen - NUR aus dem Cache.

Dieses Modul trennt zwei Dinge strikt:
  - Background-Pfad: normalisiert nicht-kollaborative Satelliten-Detektionen
    und matched sie gegen zeitnahe AIS-Pings im selben H3-Cell.
  - Request-Pfad: liest nur gecachte Ghost-Ship-Detektionen und reichert
    Vessel-Objekte additiv an. Kein Netzwerk, kein Szene-Download, kein Redis.

Quellen / Lizenz (frei, offiziell):
  - Copernicus Sentinel-1 SAR (ESA/EU) - offene Lizenz. Zugriff via Copernicus
    Data Space Ecosystem (https://dataspace.copernicus.eu).
  - VIIRS Nighttime Lights / Boat Detection (NASA Earthdata / NOAA) - offen,
    Login erforderlich (https://www.earthdata.nasa.gov).

So laeuft der Worker konzeptionell:
  1. Fuer eine bbox + Datum die passende(n) Sentinel-1-Szene(n) finden/holen.
  2. SAR-Detektor ueber die Szene laufen lassen -> Liste von Schiffs-Detektionen.
     Analog: VIIRS boat detections oder optische Ship-Detektionen laden.
  3. Detektionen gegen zeitnahe AIS-Positionen matchen:
       SAR_Detection(x,y,t) AND NOT EXISTS AIS_Ping(same H3 cell, t +/- 15min)
  4. Detektionen OHNE AIS-Match = "dark vessels" -> mit Position/Zeit cachen.
  5. Request-Pfad liest die gecachten dark-vessel-Punkte (read_cache), nie live.
"""

from __future__ import annotations

import hashlib
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..data_cache import read_cache, write_cache

SOURCE = "dark_vessels"
DEFAULT_H3_RESOLUTION = int(os.environ.get("DARK_VESSEL_H3_RESOLUTION", "7"))
MATCH_WINDOW_MINUTES = float(os.environ.get("DARK_VESSEL_MATCH_WINDOW_MINUTES", "15"))
DEFAULT_LOOKUP_RADIUS_DEG = float(os.environ.get("DARK_VESSEL_LOOKUP_RADIUS_DEG", "0.05"))
BBox = Tuple[float, float, float, float]

_detections: Optional[List[Dict[str, Any]]] = None


def _parse_time(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _lat_lon(record: Dict[str, Any]) -> tuple[Optional[float], Optional[float]]:
    lat = next(
        (record[key] for key in ("lat", "latitude", "decimalLatitude") if record.get(key) is not None),
        None,
    )
    lon = next(
        (record[key] for key in ("lon", "longitude", "decimalLongitude") if record.get(key) is not None),
        None,
    )
    try:
        return float(lat), float(lon)
    except (TypeError, ValueError):
        return None, None


def _cell_id(lat: float, lon: float, resolution: int = DEFAULT_H3_RESOLUTION) -> str:
    """Return an H3 cell when h3 is installed, else a stable grid-cell fallback."""
    try:
        import h3  # type: ignore

        if hasattr(h3, "latlng_to_cell"):
            return str(h3.latlng_to_cell(lat, lon, resolution))
        return str(h3.geo_to_h3(lat, lon, resolution))
    except Exception:  # noqa: BLE001 - h3 is optional in the lean backend install
        step = 1.0 / (2 ** max(resolution - 3, 0))
        return f"grid:{resolution}:{math.floor(lat / step)}:{math.floor(lon / step)}"


def _detection_id(record: Dict[str, Any], lat: float, lon: float, timestamp: str) -> str:
    explicit = record.get("id") or record.get("scene_id") or record.get("detection_id")
    if explicit:
        return str(explicit)
    raw = f"{record.get('source') or record.get('sensor')}|{lat:.5f}|{lon:.5f}|{timestamp}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def normalize_detection(
    record: Dict[str, Any],
    *,
    resolution: int = DEFAULT_H3_RESOLUTION,
) -> Optional[Dict[str, Any]]:
    """Normalize SAR/VIIRS/optical detections to the cache schema."""
    lat, lon = _lat_lon(record)
    ts = _parse_time(
        record.get("timestamp")
        or record.get("time")
        or record.get("acquired_at")
        or record.get("datetime")
    )
    if lat is None or lon is None or ts is None:
        return None

    source_type = str(record.get("source_type") or record.get("sensor") or "satellite").lower()
    if "viirs" in source_type:
        source_type = "viirs"
    elif "sar" in source_type or "sentinel-1" in source_type or "sentinel_1" in source_type:
        source_type = "sar"
    elif "optical" in source_type or "sentinel-2" in source_type or "planet" in source_type:
        source_type = "optical"

    iso_ts = ts.isoformat().replace("+00:00", "Z")
    cell = _cell_id(lat, lon, resolution)
    return {
        "id": _detection_id(record, lat, lon, iso_ts),
        "lat": lat,
        "lon": lon,
        "timestamp": iso_ts,
        "source_type": source_type,
        "provider": record.get("provider") or record.get("platform") or record.get("source"),
        "confidence": float(record.get("confidence", 0.7)),
        "h3_resolution": resolution,
        "h3_cell": cell,
        "ais_matches": int(record.get("ais_matches", 0)),
    }


def _ais_ping_cell_time(
    ping: Dict[str, Any],
    *,
    resolution: int,
) -> tuple[Optional[str], Optional[datetime]]:
    lat, lon = _lat_lon(ping)
    ts = _parse_time(ping.get("timestamp") or ping.get("time") or ping.get("last_seen"))
    if lat is None or lon is None or ts is None:
        return None, None
    return _cell_id(lat, lon, resolution), ts


def detect_ghost_ships(
    satellite_detections: Iterable[Dict[str, Any]],
    ais_pings: Iterable[Dict[str, Any]],
    *,
    resolution: int = DEFAULT_H3_RESOLUTION,
    match_window_minutes: float = MATCH_WINDOW_MINUTES,
) -> List[Dict[str, Any]]:
    """Return satellite detections that have no active AIS ping in the same cell."""
    window = timedelta(minutes=match_window_minutes)
    ais_index: dict[str, list[datetime]] = {}
    for ping in ais_pings:
        cell, ts = _ais_ping_cell_time(ping, resolution=resolution)
        if cell is None or ts is None:
            continue
        ais_index.setdefault(cell, []).append(ts)

    ghosts: List[Dict[str, Any]] = []
    for raw in satellite_detections:
        det = normalize_detection(raw, resolution=resolution)
        if det is None:
            continue
        det_ts = _parse_time(det["timestamp"])
        if det_ts is None:
            continue
        match_count = sum(
            1
            for ais_ts in ais_index.get(det["h3_cell"], [])
            if abs(ais_ts - det_ts) <= window
        )
        if match_count == 0:
            ghost = dict(det)
            ghost["ais_matches"] = 0
            ghost["match_window_minutes"] = match_window_minutes
            ghosts.append(ghost)
    return ghosts


def write_ghost_detections(detections: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Persist already-computed ghost detections for cache-only request use."""
    normalized = [
        det for det in (normalize_detection(d) for d in detections) if det is not None
    ]
    write_cache(SOURCE, normalized)
    global _detections
    _detections = normalized
    return {"source": SOURCE, "detections": len(normalized)}


def get_cached_dark_vessels() -> List[Dict[str, Any]]:
    """Request-Pfad: liest gecachte dark-vessel-Detektionen (oder leer).

    Reiner Cache-Lookup. Solange der Worker nichts produziert hat -> leere Liste.
    """
    data = read_cache(SOURCE)
    return data if isinstance(data, list) else []


def warmup() -> None:
    global _detections
    _detections = get_cached_dark_vessels()


def refresh() -> Dict[str, Any]:
    """Refresh hook for the generic scheduler.

    Real satellite ingestion is intentionally external to this generic refresh
    command. This keeps an empty cache present and reports the current count.
    """
    _seed_empty_cache()
    warmup()
    return {"source": SOURCE, "detections": len(_detections or [])}


def lookup(
    lat: float,
    lon: float,
    radius_deg: float = DEFAULT_LOOKUP_RADIUS_DEG,
) -> Dict[str, Any]:
    """Cache-only lookup of ghost detections near a point."""
    global _detections
    if _detections is None:
        _detections = get_cached_dark_vessels()

    nearby: List[Dict[str, Any]] = []
    for det in _detections:
        dlat, dlon = _lat_lon(det)
        if dlat is None or dlon is None:
            continue
        if abs(dlat - lat) <= radius_deg and abs(dlon - lon) <= radius_deg:
            nearby.append(det)

    sources = sorted({str(d.get("source_type", "satellite")) for d in nearby})
    return {
        "dark_detection_count": len(nearby),
        "dark_detection_sources": sources,
        "nearest_dark_detection_nm": _nearest_nm(lat, lon, nearby),
        "detections": nearby[:10],
    }


def _nearest_nm(lat: float, lon: float, detections: List[Dict[str, Any]]) -> float:
    best = -1.0
    for det in detections:
        dlat, dlon = _lat_lon(det)
        if dlat is None or dlon is None:
            continue
        nm = math.hypot((dlat - lat) * 60.0, (dlon - lon) * 60.0)
        if best < 0 or nm < best:
            best = nm
    return best


def enrich_vessels(vessels: Iterable[Any]) -> None:
    """Attach nearby ghost-detection context to Vessel-like objects in place."""
    for vessel in vessels:
        context = lookup(vessel.lat, vessel.lon)
        vessel.dark_detection_count = context["dark_detection_count"]
        vessel.dark_detection_sources = context["dark_detection_sources"]
        vessel.nearest_dark_detection_nm = context["nearest_dark_detection_nm"]


def detections_as_vessels() -> List[Any]:
    """Represent cached ghost detections as Vessel objects for map/ranking APIs."""
    from ..risk_engine import Vessel  # lazy import avoids a module import cycle

    vessels: List[Any] = []
    for det in get_cached_dark_vessels():
        lat, lon = _lat_lon(det)
        if lat is None or lon is None:
            continue
        source_type = str(det.get("source_type") or "satellite")
        vessels.append(Vessel(
            mmsi=f"ghost:{det.get('id', 'unknown')}",
            name=f"Unidentified {source_type.upper()} target",
            lat=lat,
            lon=lon,
            speed_knots=0.0,
            vessel_type="unknown",
            sanctions_check=False,
            dark_detection_count=1,
            dark_detection_sources=[source_type],
            nearest_dark_detection_nm=0.0,
        ))
    return vessels


def run_worker(bbox: BBox, date: str) -> Dict[str, Any]:
    """ASYNC Background-Worker (GERUEST) - NICHT aus einem Request aufrufen.

    Echte Implementierung folgt dem Pseudo-Ablauf im Modul-Docstring. Aktuell
    bewusst ein No-op-Stub, der klar macht, dass hier noch nichts rechnet.
    """
    raise NotImplementedError(
        "SAR/VIIRS dark-vessel worker ist ein Geruest - eigene Phase. "
        "Siehe Modul-Docstring fuer den geplanten Ablauf."
    )


def _seed_empty_cache() -> None:
    """Legt einen leeren Cache an, damit der Request-Pfad konsistent liest."""
    if read_cache(SOURCE) is None:
        write_cache(SOURCE, [])
