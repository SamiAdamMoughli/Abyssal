"""Offizielle IUU-Schiffslisten als Ground-Truth-Labels.

NUR offizielle, veroeffentlichte Listen von Behoerden/RFMOs - keine selbst
abgeleiteten Verdachtsmomente. Ein Eintrag hier bedeutet: eine autoritative Stelle
fuehrt dieses Schiff als IUU. Das ist ein Fakt einer Behoerde, kein Systemurteil
(vgl. ARCHITECTURE.md, Zone A / "Offizielle Listen != eigener Verdacht").

Datenquelle (verifiziert, Stand der Recherche):
  CCAMLR Non-Contracting Party IUU Vessel List 2025/26
  https://www.ccamlr.org/en/compliance/non-contracting-party-iuu-vessel-list

Rohdaten: backend/data/iuu_official.json (Eintraege 1:1 aus der offiziellen Liste).

EHRLICHKEIT:
  - IMO + Namen stammen direkt aus der offiziellen Liste (verifiziert).
  - CCAMLR veroeffentlicht KEINE MMSI -> mmsi ist None ("unverified").
  - Die CCAMLR-Contracting-Party-Liste war 2025/26 leer.
  - ICCAT/IOTC/SPRFMO/NPFC publizieren ihre Listen als PDF und sind hier (noch)
    NICHT enthalten -> der Datensatz ist bewusst CCAMLR-fokussiert und erweiterbar.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "iuu_official.json"


@dataclass
class IUUVessel:
    """Ein offiziell als IUU gefuehrtes Schiff (autoritatives Positiv-Label)."""

    name: str
    imo: Optional[str]
    mmsi: Optional[str]          # None = von der Quelle nicht publiziert (unverified)
    flag: str
    listing_source: str          # z. B. "CCAMLR"
    listing_year: Optional[int]
    status: str                  # z. B. "listed"
    aliases: List[str] = field(default_factory=list)

    def all_names(self) -> List[str]:
        """Aktueller Name + alle frueheren Namen/Aliasse (fuer Name-Matching)."""
        return [self.name, *self.aliases]


def load_iuu_vessels() -> List[IUUVessel]:
    """Laedt die offiziellen IUU-Eintraege aus der JSON-Rohdatei."""
    with open(DATA_PATH, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    out: List[IUUVessel] = []
    for v in raw.get("vessels", []):
        out.append(IUUVessel(
            name=v["name"],
            imo=v.get("imo"),
            mmsi=v.get("mmsi"),
            flag=v.get("flag", "Unknown"),
            listing_source=v.get("listing_source", "?"),
            listing_year=v.get("listing_year"),
            status=v.get("status", "listed"),
            aliases=v.get("aliases", []),
        ))
    return out


def source_note() -> str:
    """Kurze Quellen-/Stand-Notiz fuer Reports."""
    with open(DATA_PATH, "r", encoding="utf-8") as fh:
        meta = json.load(fh).get("_meta", {})
    return "; ".join(meta.get("sources", [])) or "offizielle IUU-Listen"


if __name__ == "__main__":
    vessels = load_iuu_vessels()
    print(f"{len(vessels)} offizielle IUU-Eintraege geladen ({source_note()})")
    for v in vessels[:5]:
        print(f"  - {v.name} (IMO {v.imo}, {v.listing_source} {v.listing_year}, "
              f"{len(v.aliases)} Aliasse)")
