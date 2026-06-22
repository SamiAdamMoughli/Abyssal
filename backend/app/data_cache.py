"""Generischer lokaler Datei-Cache fuer STATISCHE Datenquellen.

Designziel: STATISCHE Daten (Schutzgebiete, EEZ, IUU-Listen, Sanktionen,
Hafeninspektionen) werden EINMAL geladen, lokal gespeichert und per
Hintergrund-Job aktualisiert. Im Request-Pfad findet NUR ein Cache-Lookup statt -
nie ein Netzwerk-Call. Das haelt die API schnell.

Cache liegt als JSON unter backend/data/cache/<source>.json mit Zeitstempel.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Callable, Optional

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"
logger = logging.getLogger("mission_radar.cache")


def _path(source_name: str) -> Path:
    return CACHE_DIR / f"{source_name}.json"


def _age_hours(path: Path) -> float:
    return (time.time() - path.stat().st_mtime) / 3600.0


def read_cache(source_name: str) -> Optional[Any]:
    """Liest die gecachten Daten (oder None). Reiner Datei-Lookup, kein Netzwerk."""
    path = _path(source_name)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh).get("data")
    except (ValueError, OSError) as exc:
        logger.warning("Cache %s unlesbar: %s", source_name, exc)
        return None


def write_cache(source_name: str, data: Any) -> None:
    """Schreibt Daten + Zeitstempel in den Cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"fetched_at": time.time(), "data": data}
    tmp = _path(source_name).with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    tmp.replace(_path(source_name))   # atomar


def get_or_fetch(source_name: str, fetch_fn: Callable[[], Any],
                 max_age_hours: float, force: bool = False) -> Any:
    """Liefert gecachte Daten, wenn frisch genug; sonst neu laden + cachen.

    - Cache frisch (Alter < max_age_hours) und nicht force -> Cache-Treffer.
    - Sonst fetch_fn() aufrufen, Ergebnis cachen, zurueckgeben.
    - Schlaegt fetch_fn fehl, aber ein (veralteter) Cache existiert -> liefert den
      alten Stand (graceful) und loggt. Ohne Cache wird der Fehler durchgereicht.

    WICHTIG: Diese Funktion gehoert in den BACKGROUND-/Init-Pfad, nicht in jeden
    Request. Im Request liest man mit read_cache() (reiner Lookup).
    """
    path = _path(source_name)
    if not force and path.exists() and _age_hours(path) < max_age_hours:
        cached = read_cache(source_name)
        if cached is not None:
            return cached

    try:
        data = fetch_fn()
    except Exception as exc:   # noqa: BLE001 - bewusst breit: jede Quelle kann anders failen
        stale = read_cache(source_name)
        if stale is not None:
            logger.warning("Quelle %s fehlgeschlagen (%s) - nutze alten Cache.",
                           source_name, exc)
            return stale
        logger.error("Quelle %s fehlgeschlagen und kein Cache vorhanden: %s",
                     source_name, exc)
        raise

    write_cache(source_name, data)
    return data


def cache_info(source_name: str) -> dict:
    """Status fuer den Refresh-Report (existiert? wie alt?)."""
    path = _path(source_name)
    if not path.exists():
        return {"source": source_name, "cached": False, "age_hours": None}
    return {"source": source_name, "cached": True,
            "age_hours": round(_age_hours(path), 1)}
