"""Ehrlicher Validierungsreport: Risk Score gegen offizielle IUU-Listen.

Kreuzt zwei Dinge:
  - IDENTITAET (Ground Truth): die offizielle IUU-Liste (iuu_official_list.py).
  - VERHALTEN (Score): die behavioralen Fixtures aus known_cases.py, durch die
    UNVERAENDERTE Engine bewertet.

Die offizielle Liste sagt, WER offiziell IUU ist; die Engine sagt, ob ihr Score
das VERHALTEN auffaengt. Beide getrennt zu halten ist der Kern der Ehrlichkeit.

Ausfuehren (aus backend/):
    .venv/bin/python -m validation.validation_report
"""

from __future__ import annotations

from collections import Counter
from typing import List

from app.risk_engine import assess
from validation.iuu_official_list import load_iuu_vessels, source_note
from validation.known_cases import KNOWN_CASES
from validation.label_matching import match_against_iuu

HIGH_RISK = 60.0          # "Score > 60" gemaess Aufgabenstellung
LINE = "=" * 74
THIN = "-" * 74


def main() -> None:
    iuu = load_iuu_vessels()
    # "Geladene Schiffe" fuer diesen Report = die behavioralen IUU-Fixtures
    # (sie tragen Score-relevantes Verhalten). In Produktion wuerde man hier
    # live GFW-Vessels uebergeben - siehe Limitations.
    cases = KNOWN_CASES
    vessels = [c.vessel for c in cases]
    assessments = {c.vessel.name: assess(c.vessel) for c in cases}

    print(LINE)
    print("OFFIZIELLE IUU-LISTE ALS GROUND TRUTH")
    print(LINE)
    print(f"  Quelle: {source_note()}")
    print(f"  Offizielle Eintraege geladen: {len(iuu)}")

    # ---- Identitaets-Match: welche Fixtures stehen auf der offiziellen Liste? ----
    matches = match_against_iuu(vessels, iuu)
    matched_names = {m.vessel.name for m in matches}
    print("\n" + LINE)
    print("IDENTITAETS-ABGLEICH (Fixture <-> offizielle Liste)")
    print(LINE)
    for m in matches:
        a = assessments[m.vessel.name]
        print(f"  ✓ {m.vessel.name:26s} -> {m.iuu.name} "
              f"[{m.match_type}, sim={m.similarity:.2f}] "
              f"({m.iuu.listing_source} {m.iuu.listing_year}); Score {a.score:.0f}")
    not_matched = [c for c in cases if c.vessel.name not in matched_names]
    for c in not_matched:
        tag = "Kontrolle" if not c.expected_high_risk else "dok. IUU, nicht auf akt. Liste"
        print(f"  – {c.vessel.name:26s} -> kein Match auf akt. Liste ({tag})")

    # ---- Recall A: nur offiziell-bestaetigte Fixtures ----
    confirmed = [assessments[n] for n in matched_names]
    rec_a = sum(1 for a in confirmed if a.score > HIGH_RISK)
    # ---- Recall B: alle DOKUMENTIERTEN IUU-Fixtures (breiter) ----
    documented = [assessments[c.vessel.name] for c in cases if c.expected_high_risk]
    rec_b = sum(1 for a in documented if a.score > HIGH_RISK)

    print("\n" + LINE)
    print("RECALL  (Score > 60)")
    print(LINE)
    print(f"  A) offiziell (CCAMLR) bestaetigte Faelle: "
          f"{rec_a}/{len(confirmed)} erkannt")
    print(f"  B) alle dokumentierten IUU-Fixtures:      "
          f"{rec_b}/{len(documented)} erkannt "
          f"({rec_b/len(documented)*100:.0f}%)")

    # ---- Die wichtigen Faelle: niedrig gescorte bekannte IUU ----
    print("\n  Bekannte IUU-Faelle mit NIEDRIGEM Score (<= 60) - die wichtigen:")
    low = [(c, assessments[c.vessel.name]) for c in cases
           if c.expected_high_risk and assessments[c.vessel.name].score <= HIGH_RISK]
    if not low:
        print("    (keine)")
    for c, a in low:
        fired = ", ".join(r.label for r in a.reasons) or "keine Regel"
        print(f"    - {c.vessel.name}: Score {a.score:.0f} | feuerte: {fired}")
        print(f"      Warum: {c.notes}")

    # ---- Welche Regeln treffen bei echten IUU am haeufigsten? ----
    print("\n  Regel-Haeufigkeit bei dokumentierten IUU-Faellen:")
    freq = Counter()
    for a in documented:
        for r in a.reasons:
            freq[r.label] += 1
    for label, n in freq.most_common():
        print(f"    {n:3d}x  {label}")

    # ---- Score-Verteilung: bekannte IUU vs. Rest ----
    print("\n  Score-Verteilung:")
    iuu_scores = [a.score for a in documented]
    other_scores = [assessments[c.vessel.name].score for c in cases
                    if not c.expected_high_risk]
    avg = lambda xs: sum(xs) / len(xs) if xs else 0.0
    print(f"    bekannte IUU (n={len(iuu_scores)}): "
          f"Ø {avg(iuu_scores):.0f}, min {min(iuu_scores):.0f}, max {max(iuu_scores):.0f}")
    print(f"    Kontrolle    (n={len(other_scores)}): "
          f"Ø {avg(other_scores):.0f}" if other_scores else "    Kontrolle: keine")

    _print_limitations()


def _print_limitations() -> None:
    print("\n" + LINE)
    print("LIMITATIONS  (Pflicht - diese Ehrlichkeit ist kein Zusatz)")
    print(LINE)
    msgs = [
        "Catch-Bias: Bekannte IUU-Schiffe sind verzerrt - nur die ERWISCHTEN "
        "stehen auf Listen. Schiffe, die nie aufflogen, fehlen komplett.",
        "Kaum saubere Negativ-Labels: Wir haben Positiv-Labels, aber kein "
        "verlaesslicher Datensatz bestaetigt-legaler Schiffe als Gegenprobe.",
        "Ein Schiff NICHT auf der Liste ist NICHT bewiesen legal.",
        "Recall gegen bekannte Faelle != echte Treffsicherheit in der Praxis.",
        "Winzige Stichprobe: Recall beruht auf wenigen Fixtures - statistisch "
        "nicht belastbar, nur eine Plausibilitaetspruefung.",
        "Behaviorale Werte der Fixtures sind APPROXIMATIONEN dokumentierten "
        "Verhaltens (known_cases.py), keine echten AIS-Traces.",
        "Identifier-Luecke: Die offizielle Liste nutzt IMO, die Pipeline nutzt "
        "MMSI/Name. Echtes IMO-Matching gegen Live-GFW braucht IMO im Vessel.",
        "Defunkte Schiffe: Viele gelistete Poacher sind gesunken/umgeflaggt und "
        "senden kein AIS mehr -> Live-Matching gegen aktuelle Daten ist spaerlich.",
    ]
    for m in msgs:
        print(f"  - {m}")
    print(THIN)
    print("  Fazit: Offizielle Listen geben echte Positiv-Labels und bestaetigen")
    print("  die Identitaet der Faelle - aber sie ersetzen keine ausgewogene")
    print("  Ground Truth. Der Score bleibt eine Hypothese, jetzt gegen echte")
    print("  Fakten gespiegelt statt gegen Bauchgefuehl.")


if __name__ == "__main__":
    main()
