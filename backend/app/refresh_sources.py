"""Background-Refresh der STATISCHEN Datenquellen.

Aktualisiert alle gecachten offiziellen Quellen (IUU/Sanktionen/PSC/EEZ). Gehoert
NICHT in den Request-Pfad - per Cron/Scheduler aufrufen:

    cd backend && python -m app.refresh_sources

Beispiel-Cron (taeglich 03:00, kein Setup hier noetig - nur Doku):
    0 3 * * *  cd /pfad/backend && .venv/bin/python -m app.refresh_sources

Loggt pro Quelle: vorheriges Alter, neuer Stand, Eintragszahl, Fehler.
"""

from __future__ import annotations

import logging

from .data_cache import cache_info
from .sources import eez, iuu_list, opensanctions, port_control

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("mission_radar.refresh")

SOURCES = [iuu_list, opensanctions, port_control, eez]


def main() -> None:
    print("Refresh statischer Quellen")
    print("=" * 60)
    for mod in SOURCES:
        before = cache_info(mod.SOURCE)
        age = f"{before['age_hours']}h" if before["cached"] else "kein Cache"
        try:
            result = mod.refresh()
            if mod.SOURCE == "opensanctions_vessels":
                msg = (f"OpenSanctions: {result['vessels']} vessels loaded, "
                       f"{result['sources']} sanctions sources")
            else:
                msg = f"Eintraege: {list(result.values())[-1]}"
            print(f"  [OK ] {mod.SOURCE:22s} vorher: {age:>12s} | {msg}")
        except Exception as exc:  # noqa: BLE001
            print(f"  [ERR] {mod.SOURCE:14s} vorher: {age:>12s} | Fehler: {exc}")
            log.error("Refresh %s fehlgeschlagen: %s", mod.SOURCE, exc)
    print("=" * 60)
    print("Hinweis: Leere Quellen (0 Eintraege) sind erwartet, solange keine")
    print("echten Roh-Daten in backend/data/sources/ eingespeist wurden.")


if __name__ == "__main__":
    main()
