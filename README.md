# 🛰️ Mission Radar

Ein **Decision-Support-System**, das mögliche Ziele bei der Bekämpfung illegaler
Fischerei priorisiert — und dabei **immer erklärt, warum**. Vorbilder: Global
Fishing Watch, Sea Shepherd. Naturschutz-Projekt.

> Mission Radar ist **kein** reines Daten-Dashboard. Es soll erklärbar sagen:
> *„Hier sind die wahrscheinlichsten Ziele, und das ist der Grund."*
> Erklärbarkeit hat Vorrang vor Cleverness.

---

## Architektur

```
   ┌─────────────────────┐     ┌───────────────┐     ┌───────────┐     ┌──────────────┐
   │   Datenquelle       │     │  Risk Engine  │     │  FastAPI  │     │   Frontend   │
   │  (Phase 1:          │ ──▶ │  (regel-      │ ──▶ │  (REST,   │ ──▶ │  (Leaflet,   │
   │   synthetisch)      │     │   basiert)    │     │   JSON)   │     │   kein Build)│
   │                     │     │               │     │           │     │              │
   │  sample_data.py     │     │ risk_engine.py│     │  main.py  │     │  index.html  │
   └─────────────────────┘     └───────────────┘     └───────────┘     └──────────────┘
        AUSTAUSCHBAR              FEST / STABIL
```

**Wichtigster Designgrundsatz: Die Datenquelle ist austauschbar, die Engine nicht.**

Die Engine (`risk_engine.py`) hängt nur von der `Vessel`-Datenklasse ab — nie von
einer konkreten Datenquelle. Die Datenquelle erfüllt das `VesselSource`-Protokoll
und liefert `List[Vessel]`. In **Phase 2** wird ausschließlich die Datenquelle
ersetzt (z. B. durch einen Global-Fishing-Watch-API-Adapter), indem `get_source()`
in [sample_data.py](backend/app/sample_data.py) eine andere Implementierung
zurückgibt. **Engine und API bleiben unangetastet.**

### Projektstruktur

```
Abyssal/
├── backend/
│   └── app/
│       ├── risk_engine.py   # Herzstück: Regeln, Vessel, Scoring, rank_targets
│       ├── sample_data.py   # austauschbare Datenquelle (synthetisch)
│       └── main.py          # FastAPI-Endpunkte (dünn, ohne Fachlogik)
├── frontend/
│   └── index.html           # Leaflet-Karte + Target-Liste, kein Build-Step
└── README.md
```

---

## Setup

Voraussetzung: **Python 3.14** (siehe [.python-version](.python-version)).

```bash
# 1. ins Backend wechseln
cd backend

# 2. virtuelle Umgebung
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. Abhängigkeiten
pip install fastapi uvicorn

# 4. API starten (aus dem Ordner backend/)
uvicorn app.main:app --reload
```

Die API läuft dann auf <http://localhost:8000>.

| Endpoint        | Zweck                                             |
| --------------- | ------------------------------------------------- |
| `GET /`         | Health-Check                                      |
| `GET /api/targets` | Top-N Ziele mit Begründung (gerankt)           |
| `GET /api/vessels` | alle Schiffe mit Score (für die Karte)         |

**Frontend:** [frontend/index.html](frontend/index.html) im Browser öffnen
(Doppelklick reicht — CORS ist offen). Ist das Backend nicht gestartet, zeigt die
Seite eine klare Fehlermeldung statt einer leeren Karte.

---

## Eine neue Regel hinzufügen

Das ist bewusst trivial — der ganze Punkt des Designs. In
[risk_engine.py](backend/app/risk_engine.py):

**1.** Funktion `Vessel -> Optional[RiskReason]` schreiben. Eine Begründung ist
Pflicht (`label` fürs Badge, `detail` für den Tooltip):

```python
def rule_high_seas_transshipment(v: Vessel) -> Optional[RiskReason]:
    """Beispiel: Umladung auf hoher See ist ein bekanntes IUU-Muster."""
    if v.loitering_hours >= 6 and v.ais_gap_hours >= 4:
        return RiskReason(
            points=20,
            label="Transshipment-Verdacht",
            detail="Langes Verweilen kombiniert mit AIS-Lücke deutet auf eine "
                   "Umladung auf hoher See hin.",
        )
    return None
```

**2.** Die Funktion an die `RULES`-Liste anhängen:

```python
RULES: List[Rule] = [
    rule_protected_area,
    rule_fishing_speed,
    rule_ais_gap,
    rule_loitering,
    rule_high_seas_transshipment,   # <- neu
]
```

Fertig. **Kein anderer Code wird angefasst.** Die Engine, die API und das Frontend
übernehmen die neue Regel automatisch (inkl. Badge im UI).

### Startregeln (Phase 1)

| Regel                          | Punkte | Begründung                                  |
| ------------------------------ | ------ | ------------------------------------------- |
| Im Schutzgebiet                | +35    | Aufenthalt in einem MPA                     |
| Fischerei-Tempo (2–5 kn)       | +20    | typische Schleppnetz-Geschwindigkeit        |
| AIS-Lücke ≥ 12 h               | +25    | „going dark", Verschleierung                |
| AIS-Lücke ≥ 4 h                | +10    | moderate Lücke, beobachtenswert             |
| Verweilen (Loitering) ≥ 6 h    | +15    | Fang/Umladen statt Transit                  |

Score = Summe aller zutreffenden Begründungen, **gedeckelt bei 100**. Schiffe ohne
eine einzige Begründung erscheinen nicht in der Ziel-Liste.

---

## ⚠️ Validierung — ehrlich gesagt

**Die Score-Gewichte sind geschätzt, nicht gemessen.** Sie beruhen auf Plausibilität
und gängigem Domänenwissen über IUU-Muster — nicht auf einer Auswertung echter Fälle.
In der jetzigen Form ist Mission Radar ein **Demonstrator / Prototyp**, kein
einsatzreifes Werkzeug. Bevor dieses System reale Entscheidungen stützt, braucht es:

1. **Echte AIS-Daten** statt synthetischer Szenen — z. B. über die
   [Global Fishing Watch API](https://globalfishingwatch.org/our-apis/). Das ist
   genau der Phase-2-Schritt, für den die Datenquelle austauschbar gebaut ist.
2. **Ground Truth:** ein Datensatz bekannter, bestätigter IUU-Fälle (und bestätigter
   Negativfälle) als Maßstab. Ohne bekannte Wahrheit lässt sich kein Gewicht
   rechtfertigen.
3. **Fehlerraten-Analyse:** False-Positive- und False-Negative-Raten messen. Ein
   Decision-Support-System, das legitime Fischer fälschlich verdächtigt, richtet
   realen Schaden an — das muss quantifiziert und akzeptabel sein.
4. **Kalibrierung der Gewichte** gegen diese Ground Truth (statt sie zu raten),
   idealerweise mit Sensitivitätsanalyse: Wie stark hängt das Ranking an einzelnen
   Gewichten?
5. **Domänen-Review:** Die Regeln und Schwellen gehören von Menschen mit
   Fischerei-/Enforcement-Erfahrung geprüft.

Bis dahin gilt: Die Begründungen sind belastbarer als die Zahlen. Mission Radar
**priorisiert Aufmerksamkeit** und macht seine Annahmen transparent — es trifft
keine Schuldfeststellung.
