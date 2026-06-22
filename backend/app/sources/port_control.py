"""STATISCHE Quelle: Port-State-Control-Detentions (Paris/Tokyo MOU), Cache-only.

Quelle / Lizenz:
  - Paris MoU (https://www.parismou.org) - detentions/banned vessels (oeffentlich).
  - Tokyo MoU (https://www.tokyo-mou.org) - detention list (oeffentlich).
  Beide veroeffentlichen Inspektions-/Detention-Historien pro Schiff (IMO).

EHRLICH: In diesem Schritt KEINE Detention-Daten gebundelt (keine erfundenen
Eintraege). Ohne lokale Datei -> leere Liste -> rule_port_detention feuert nie.
Echte Daten via refresh_sources einspeisen (Scraper/Export der MoU-Listen).

Aktualisierungsrhythmus: statisch -> max_age 168h (woechentlich).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..data_cache import get_or_fetch

SOURCE = "port_control"
MAX_AGE_H = 168.0
_LOCAL = Path(__file__).resolve().parent.parent.parent / "data" / "sources" / "psc_detentions.json"

_index: Optional[Dict[str, Any]] = None


def fetch_detentions() -> List[Dict[str, Any]]:
    """Laedt Detention-Records (lokal, sonst leer)."""
    if not _LOCAL.exists():
        return []
    with open(_LOCAL, "r", encoding="utf-8") as fh:
        return json.load(fh)


def refresh() -> Dict[str, Any]:
    data = get_or_fetch(SOURCE, fetch_detentions, MAX_AGE_H, force=True)
    global _index
    _index = None
    return {"source": SOURCE, "entries": len(data)}


def _build_index() -> Dict[str, Any]:
    data = get_or_fetch(SOURCE, fetch_detentions, MAX_AGE_H)
    by_imo = {}
    for e in data:
        if e.get("imo"):
            by_imo.setdefault(str(e["imo"]).strip(), []).append(e)
    return {"by_imo": by_imo, "count": len(data)}


def warmup() -> None:
    global _index
    _index = _build_index()


def lookup(imo: Optional[str]) -> Optional[Dict[str, Any]]:
    """Cache-only Lookup: hat das Schiff (IMO) eine Detention-Historie?"""
    global _index
    if _index is None:
        _index = _build_index()
    if imo and str(imo).strip() in _index["by_imo"]:
        recs = _index["by_imo"][str(imo).strip()]
        return {"detentions": len(recs)}
    return None
