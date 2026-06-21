"""Validierung des Risk Scores gegen bekannte IUU-Faelle.

Schickt die dokumentierten Faelle aus known_cases.py durch die UNVERAENDERTE
Risk Engine und prueft, ob bekannte Hochrisiko-Faelle hoch bewertet werden.

Ausfuehren (aus dem Ordner backend/):
    .venv/bin/python -m validation.validate_scores

Die Engine wird nur aufgerufen, nie veraendert.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from app.risk_engine import TargetAssessment, assess, rank_targets
from validation.known_cases import KNOWN_CASES, KnownCase

# Schwelle, ab der ein Score als "hochriskant" gilt. Bewusst gewaehlt (nicht
# gemessen): >= 50 bedeutet mind. zwei starke Signale (z. B. Schutzgebiet +35
# und eine weitere Regel). Aenderbar - die Wahl beeinflusst TP/FP direkt.
HIGH_RISK_THRESHOLD = 50.0

# Spaltenbreiten / Trenner fuer die Ausgabe
LINE = "=" * 74
THIN = "-" * 74


def classify(expected_high: bool, score: float) -> str:
    """Konfusionsmatrix-Label fuer einen Fall."""
    predicted_high = score >= HIGH_RISK_THRESHOLD
    if expected_high and predicted_high:
        return "TP"   # korrekt als Hochrisiko erkannt
    if expected_high and not predicted_high:
        return "FN"   # bekannter IUU-Fall NICHT erkannt
    if not expected_high and predicted_high:
        return "FP"   # harmloser Fall faelschlich geflaggt
    return "TN"       # korrekt als unauffaellig erkannt


def evaluate() -> List[Tuple[KnownCase, TargetAssessment, str]]:
    """Bewertet jeden Fall und gibt (Fall, Assessment, Label) zurueck."""
    results = []
    for case in KNOWN_CASES:
        assessment = assess(case.vessel)
        label = classify(case.expected_high_risk, assessment.score)
        results.append((case, assessment, label))
    return results


def print_case_details(results) -> None:
    print(LINE)
    print("EINZELFALL-AUSWERTUNG")
    print(LINE)
    for case, a, label in results:
        exp = "HOCH" if case.expected_high_risk else "niedrig"
        approx = " [approx]" if case.approximate else ""
        flag = {"TP": "OK ", "TN": "OK ", "FN": "!! ", "FP": "!! "}[label]
        print(f"\n[{flag}{label}] {case.vessel.name}")
        print(f"    erwartet: {exp:7s} | Score: {a.score:5.0f}/100{approx}")
        if a.reasons:
            print("    Regeln angeschlagen:")
            for r in a.reasons:
                print(f"      + {r.points:>4.0f}  {r.label}")
        else:
            print("    Regeln angeschlagen: (keine)")
        print(f"    Quelle: {case.source}")
        if case.notes:
            print(f"    Hinweis: {case.notes}")


def print_ranking() -> None:
    """Zeigt, wie rank_targets() die Faelle ordnen wuerde (wie im Live-System)."""
    print("\n" + LINE)
    print("RANKING (wie rank_targets() es liefern wuerde)")
    print(LINE)
    ranked = rank_targets([c.vessel for c in KNOWN_CASES], top_n=len(KNOWN_CASES))
    for i, a in enumerate(ranked, 1):
        print(f"  {i}. {a.vessel.name:38s} Score {a.score:5.0f}")
    if len(ranked) < len(KNOWN_CASES):
        print(f"  (nicht gelistet: {len(KNOWN_CASES) - len(ranked)} Fall/Faelle "
              "mit Score 0 - rank_targets schliesst sie aus)")


def rule_effectiveness(results) -> Dict[str, Dict[str, int]]:
    """Zaehlt pro Regel, wie oft sie in TP- bzw. FP-Faellen angeschlagen hat."""
    stats: Dict[str, Dict[str, int]] = {}
    for case, a, label in results:
        for r in a.reasons:
            s = stats.setdefault(r.label, {"in_TP": 0, "in_FP": 0, "total": 0})
            s["total"] += 1
            if label == "TP":
                s["in_TP"] += 1
            elif label == "FP":
                s["in_FP"] += 1
    return stats


def generate_report(results) -> None:
    """Ehrliche Gesamtauswertung: TP-Rate, Luecken, Empfehlungen, Disclaimer."""
    iuu = [(c, a, l) for (c, a, l) in results if c.expected_high_risk]
    controls = [(c, a, l) for (c, a, l) in results if not c.expected_high_risk]

    tp = sum(1 for _, _, l in iuu if l == "TP")
    fn = sum(1 for _, _, l in iuu if l == "FN")
    fp = sum(1 for _, _, l in controls if l == "FP")
    tn = sum(1 for _, _, l in controls if l == "TN")
    tp_rate = (tp / len(iuu) * 100) if iuu else 0.0

    print("\n" + LINE)
    print("ZUSAMMENFASSUNG")
    print(LINE)
    print(f"  Bekannte IUU-Faelle:        {len(iuu)}")
    print(f"  Davon korrekt erkannt (TP): {tp}")
    print(f"  Nicht erkannt (FN):         {fn}")
    print(f"  True Positive Rate:         {tp_rate:.0f}%  "
          f"(Schwelle Score >= {HIGH_RISK_THRESHOLD:.0f})")
    print(f"  Negativkontrollen:          {len(controls)}  -> TN={tn}, FP={fp}")

    # Effektivste Regeln (nach Treffern in TP-Faellen)
    stats = rule_effectiveness(results)
    print("\n  Regel-Effektivitaet (Treffer in korrekt erkannten Faellen):")
    if stats:
        for label, s in sorted(stats.items(), key=lambda kv: -kv[1]["in_TP"]):
            warn = "  <- auch in FP!" if s["in_FP"] else ""
            print(f"    {label:22s} TP-Treffer: {s['in_TP']}  "
                  f"(gesamt {s['total']}){warn}")
    else:
        print("    (keine Regel hat angeschlagen)")

    # Was der Score NICHT erkennt
    print("\n  Was der Score NICHT zuverlaessig erkennt:")
    for case, a, label in iuu:
        if label == "FN":
            print(f"    - {case.vessel.name}: Score {a.score:.0f} < Schwelle. "
                  f"{case.notes}")
    print("    - Schiffe, die AIS KOMPLETT abschalten, erzeugen GAR KEINE Daten "
          "-> sie tauchen im System nie auf (Meta-Luecke, hier nicht messbar).")
    print("    - Transshipment/Umladung und Reefer-Logistik werden von den "
          "aktuellen Speed-/Gap-Regeln kaum erfasst.")

    # Konkrete Empfehlungen
    print("\n  Empfehlungen (Engine bleibt Sache separater Aenderungen):")
    print("    1. Regel fuer Transshipment/Encounter ergaenzen (Reefer-Faelle "
          "wie Fu Yuan Yu Leng 999 wuerden sonst durchrutschen).")
    print("    2. Identitaets-/Flaggenwechsel als Signal aufnehmen (Bandit-6- "
          "und STS-50-Muster).")
    print("    3. Watchlist-Abgleich (CCAMLR/Interpol) als starkes Zusatzsignal.")
    print("    4. Schwelle und Gewichte gegen einen GROESSEREN, gelabelten "
          "Datensatz kalibrieren statt zu schaetzen.")

    # Ehrlicher Disclaimer
    print("\n" + THIN)
    print("DISCLAIMER")
    print(THIN)
    print("  Diese Validierung basiert auf SYNTHETISCHEN APPROXIMATIONEN bekannter")
    print("  Faelle, NICHT auf echten AIS-Traces. Die Eingabewerte spiegeln")
    print("  dokumentiertes Verhalten wider, sind aber geschaetzt und je Fall als")
    print("  'approx' markiert. Eine echte Validierung braucht reale AIS-Verlaeufe")
    print("  (z. B. via GFW-Token) und einen groesseren, unabhaengig gelabelten")
    print("  Datensatz mit Positiv- UND Negativfaellen.")


def main() -> None:
    results = evaluate()
    print_case_details(results)
    print_ranking()
    generate_report(results)


if __name__ == "__main__":
    main()
