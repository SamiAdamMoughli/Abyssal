"""Score-Verteilungs-Analyse auf ECHTEN GFW-Daten.

Zweck: Pruefen, ob die (geschaetzten) Regel-Gewichte auf echten Events sinnvoll
streuen - oder ob z. B. eine Regel nie anschlaegt oder alles gleich hoch landet.
Reines Lese-/Analyse-Tool: ruft Datenquelle + Engine nur auf, aendert nichts.

Ausfuehren (aus backend/):
    .venv/bin/python -m validation.score_distribution

Region/Zeitfenster ueber Umgebungsvariablen ueberschreibbar:
    DIST_BBOX="min_lon,min_lat,max_lon,max_lat"   (Default: Galapagos-Region)
    DIST_DAYS=7                                    (Default: letzte 7 Tage)
"""

from __future__ import annotations

import os
import statistics
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import List, Tuple

from app import gfw_vessels
from app.gfw_vessels import GfwApiError
from app.risk_engine import RULES, Vessel, assess

# Wird die 7-Tage-Abfrage zu duenn (Echtzeit-AIS hat Latenz), weitet das Skript
# das Fenster automatisch und sagt es klar an.
MIN_VESSELS = 30
FALLBACK_DAYS = 45
LINE = "=" * 70


def _bbox() -> Tuple[float, float, float, float]:
    raw = os.environ.get("DIST_BBOX", "-93,-3,-87,2")  # grob Galapagos-Region
    p = [float(x) for x in raw.split(",")]
    return (p[0], p[1], p[2], p[3])


def _window(days: int) -> Tuple[str, str]:
    now = datetime.now(timezone.utc)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return (now - timedelta(days=days)).strftime(fmt), now.strftime(fmt)


def _fetch_with_retry(bbox, start, end, tries=3) -> List[Vessel]:
    """Holt Vessels mit kleinem Backoff gegen transiente GFW-Fehler (503)."""
    last = None
    for i in range(tries):
        try:
            return gfw_vessels.fetch_vessels(bbox, start, end)
        except GfwApiError as exc:
            last = exc
            print(f"  ... Versuch {i+1} fehlgeschlagen: {str(exc)[:70]}")
            time.sleep(4)
    raise last  # type: ignore[misc]


def load_real_vessels() -> Tuple[List[Vessel], str]:
    """Laedt echte Vessels; weitet das Fenster, falls zu wenige Daten."""
    bbox = _bbox()
    days = int(os.environ.get("DIST_DAYS", "7"))
    start, end = _window(days)
    print(f"Lade echte Vessels: bbox={bbox}, Fenster letzte {days} Tage ...")
    vessels = _fetch_with_retry(bbox, start, end)
    note = f"letzte {days} Tage"

    if len(vessels) < MIN_VESSELS:
        print(f"  Nur {len(vessels)} Vessels (<{MIN_VESSELS}) - AIS-Latenz. "
              f"Weite auf letzte {FALLBACK_DAYS} Tage aus.")
        start, end = _window(FALLBACK_DAYS)
        vessels = _fetch_with_retry(bbox, start, end)
        note = f"letzte {FALLBACK_DAYS} Tage (7-Tage-Fenster war zu duenn)"

    return vessels, note


def analyze(vessels: List[Vessel], window_note: str) -> None:
    assessments = [assess(v) for v in vessels]
    scores = [a.score for a in assessments]

    print("\n" + LINE)
    print(f"SCORE-VERTEILUNG auf ECHTEN Daten  (n={len(scores)}, {window_note})")
    print(LINE)

    # Buckets
    buckets = {"0-20": 0, "20-40": 0, "40-60": 0, "60-80": 0, "80-100": 0}
    for s in scores:
        if s < 20:
            buckets["0-20"] += 1
        elif s < 40:
            buckets["20-40"] += 1
        elif s < 60:
            buckets["40-60"] += 1
        elif s < 80:
            buckets["60-80"] += 1
        else:
            buckets["80-100"] += 1
    n = max(len(scores), 1)
    for label, count in buckets.items():
        bar = "#" * round(count / n * 40)
        print(f"  {label:>7s} | {count:4d} ({count/n*100:5.1f}%) {bar}")

    # Statistik
    print()
    if scores:
        print(f"  Durchschnitt:        {statistics.mean(scores):6.1f}")
        print(f"  Median:              {statistics.median(scores):6.1f}")
        sd = statistics.pstdev(scores) if len(scores) > 1 else 0.0
        print(f"  Standardabweichung:  {sd:6.1f}")
        print(f"  Min / Max:           {min(scores):.0f} / {max(scores):.0f}")
        top10 = sorted(scores, reverse=True)[:max(1, len(scores) // 10)]
        print(f"  Schwelle Top 10%:    >= {min(top10):.0f}")

    # Regel-Haeufigkeit (inkl. nie feuernder Regeln) - Regeln direkt auswerten,
    # damit auch Nullen sichtbar werden. Engine bleibt unberuehrt.
    print("\n  Regel-Haeufigkeit (wie oft schlaegt jede Regel an?):")
    fire = Counter()
    labels = {}
    for v in vessels:
        for rule in RULES:
            r = rule(v)
            if r is not None:
                fire[rule.__name__] += 1
                labels[rule.__name__] = r.label
    for rule in RULES:
        name = rule.__name__
        count = fire.get(name, 0)
        lbl = labels.get(name, "(nie ausgeloest -> kein Label gesehen)")
        flag = "  <== NIE" if count == 0 else ""
        print(f"    {count:4d}x  {lbl:22s} [{name}]{flag}")

    print("\n  Lesehilfe:")
    print("    - Eine Regel mit 0x feuert in dieser Region/Zeit nie -> evtl.")
    print("      Schwelle zu streng, Datenfeld fehlt, oder Gewicht verpufft.")
    print("    - Ist fast alles im selben Bucket, trennt der Score kaum -")
    print("      dann muessen Gewichte/Schwellen kalibriert werden (Schritt 3).")


def main() -> None:
    try:
        vessels, note = load_real_vessels()
    except GfwApiError as exc:
        print(f"\nFEHLER beim Laden echter Daten: {exc}")
        return
    if not vessels:
        print("Keine Vessels geladen - Region/Fenster anpassen (DIST_BBOX/DIST_DAYS).")
        return
    analyze(vessels, note)


if __name__ == "__main__":
    main()
