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
│       ├── geo.py           # Geo-Logik: Punkt-in-Schutzgebiet (shapely/geopandas)
│       └── main.py          # FastAPI-Endpunkte (dünn, ohne Fachlogik)
│   └── data/
│       └── protected_areas.geojson  # Schutzgebiets-Polygone (Platzhalter)
├── frontend/
│   └── index.html           # Leaflet-Karte + Target-Liste, kein Build-Step
└── README.md
```

---

## Setup

Voraussetzung: **Python 3.14** (siehe [.python-version](.python-version)). Abhängigkeiten in [backend/requirements.txt](backend/requirements.txt) (`pip install -r requirements.txt`).

```bash
# 1. ins Backend wechseln
cd backend

# 2. virtuelle Umgebung
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. Abhängigkeiten
pip install -r requirements.txt

# 4. API starten (aus dem Ordner backend/)
uvicorn app.main:app --reload
```

Die API läuft dann auf <http://localhost:8000>.

| Endpoint        | Zweck                                             |
| --------------- | ------------------------------------------------- |
| `GET /`         | Health-Check                                      |
| `GET /api/targets` | Top-N Ziele mit Begründung (gerankt)           |
| `GET /api/vessels` | alle Schiffe mit Score (für die Karte)         |

Beide `/api/*`-Endpunkte akzeptieren `?source=synthetic` (Default) oder `?source=gfw`.

**Frontend:** [frontend/index.html](frontend/index.html) im Browser öffnen
(Doppelklick reicht — CORS ist offen). Ist das Backend nicht gestartet, zeigt die
Seite eine klare Fehlermeldung statt einer leeren Karte.

---

## Datenquellen: synthetisch vs. Global Fishing Watch

Mission Radar kann zwischen zwei Datenquellen umschalten — die Risk Engine bleibt
dabei identisch, sie bekommt in beiden Fällen fertige `Vessel`-Objekte:

| Quelle      | Modul                                      | Token nötig? |
| ----------- | ------------------------------------------ | ------------ |
| `synthetic` | [sample_data.py](backend/app/sample_data.py) | nein (Default) |
| `gfw`       | [gfw_vessels.py](backend/app/gfw_vessels.py) | ja           |

**Welche GFW-API?** Die Schiffs-/AIS-Daten liegen in der **Global Fishing Watch
API v3** (`gateway.api.globalfishingwatch.org/v3`) — Endpunkte für **Vessels**,
**Events** (u. a. `gap`/AIS-off und `loitering`) und **4Wings** (Präsenz/Effort).
Das ist **nicht** die Global *Forest* Watch *Data* API (`gfw_data_api.py`, nur
Schutzgebiete). [gfw_vessels.py](backend/app/gfw_vessels.py) leitet `ais_gap_hours`
aus GAP-Events und `loitering_hours` aus LOITERING-Events ab — genau die Felder,
die die Risk-Regeln speisen.

**Umschalten** — pro Request oder global:

```bash
# pro Request (Query-Parameter)
curl "http://localhost:8000/api/targets?source=gfw"

# global per Umgebungsvariable (neuer Default)
export DATA_SOURCE=gfw
```

Bei `gfw` wird eine Default-bbox (grob Galápagos) und ein Default-Zeitfenster der
letzten 48 h verwendet — beides per `GFW_BBOX` / `GFW_LOOKBACK_HOURS` (oder
`GFW_START`/`GFW_END`) konfigurierbar. Ohne Token läuft alles unverändert im
synthetischen Default weiter; eine GFW-Anfrage ohne gültigen Token scheitert
**klar** (HTTP 502 mit Klartext), nie still. Bei Rate-Limit meldet das Modul HTTP
429 mit `Retry-After`.

### GFW-Token besorgen und setzen

1. Token im GFW-Portal anlegen (Forschungs-/Non-Profit-Zugang) über
   <https://globalfishingwatch.org/our-apis/tokens>.
2. `.env` anlegen und Token eintragen — **niemals committen** (`.env` steht in
   `.gitignore`):
   ```bash
   cp .env.example .env
   # in .env: GFW_API_TOKEN=...
   ```
   Der Token wird ausschließlich aus der Umgebungsvariable `GFW_API_TOKEN`
   gelesen, nie aus dem Code.

> ⚠️ **Vor dem echten Einsatz prüfen:** Endpunkte und Auth in
> [gfw_vessels.py](backend/app/gfw_vessels.py) sind gegen die offizielle Doku
> verifiziert (Base-URL, `Bearer`-Auth, `GET /vessels/search`, `GET/POST /events`,
> Dataset `public-global-fishing-events:latest`). **Nicht** eindeutig aus der Doku
> ableitbar und daher mit `>>> an echte GFW-Antwort anpassen <<<` markiert: die
> exakten `type`-Enum-Werte für GAP/LOITERING, die POST-Body-Syntax des
> Geometrie-Filters und einige Feldpfade im Mapping. Diese gegen eine echte
> Antwort verifizieren: <https://globalfishingwatch.org/our-apis/documentation>.
> Fehlende Felder fallen auf konservative Defaults (0.0) zurück.
> `in_protected_area` kommt **nicht** von GFW, sondern aus
> [geo.py](backend/app/geo.py).

<!-- -->

> ℹ️ **AIS ist kein perfektes Signal:** AIS-Daten haben **Latenz und Lücken**
> (Abdeckung, abgeschaltete Transponder, Satelliten-Revisit). Der Risk Score
> bleibt damit eine **Hypothese**, bis er gegen Ground Truth validiert ist (siehe
> Abschnitt „Validierung"). Echte Daten machen den Score nicht automatisch wahr —
> nur überprüfbar.

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

## Schutzgebiete (`in_protected_area`)

Das Flag `in_protected_area` wird **geometrisch berechnet**, nicht hartkodiert:
[geo.py](backend/app/geo.py) lädt die Schutzgebiets-Polygone aus
[backend/data/protected_areas.geojson](backend/data/protected_areas.geojson)
(einmalig, Modul-Level-Cache) und prüft mit shapely, ob die Schiffsposition darin
liegt. Diese Geo-Logik ist bewusst von der Risk Engine getrennt — die Engine
bekommt nur den fertigen Boolean und weiß nichts über Geometrie.

> ⚠️ Die mitgelieferte GeoJSON ist **nur ein Platzhalter** mit erfundenen Polygonen,
> die zur synthetischen Testszene passen. Sie hat keinerlei reale Bedeutung.

**Echte Daten:** Die maßgebliche Quelle für Meeresschutzgebiete ist die
**World Database on Protected Areas (WDPA)**, bereitgestellt über
**Protected Planet** — <https://www.protectedplanet.net>. Für den echten Einsatz
wird `protected_areas.geojson` durch einen WDPA-Export der relevanten Gebiete
ersetzt; an `geo.py` und der Engine ändert sich dabei nichts.

### Variante: WDPA live über die Global Forest Watch Data API

Statt eines lokalen GeoJSON-Exports kann WDPA auch live abgefragt werden — über die
**Global Forest Watch *Data* API** (`gfw-data-api`, v0.3.0). Das Modul
[gfw_data_api.py](backend/app/gfw_data_api.py) kapselt das:
`is_in_protected_area(lat, lon)` schickt ein Punkt-in-Polygon-SQL an
`GET /dataset/{dataset}/{version}/query/json`.

**Key besorgen** (laut Spec):

1. `POST /auth/sign-up` (Name, E-Mail) → bestätigt deinen Zugang.
2. `POST /auth/apikey` mit `alias`, `organization`, `email` und optional `domains`
   (die Allowlist erlaubter `origin`-Werte).

**Variablen setzen** (in `.env`, niemals committen):

```bash
GFW_API_KEY=...            # API-Key (Header/Query "x-api-key")
GFW_API_ORIGIN=http://localhost   # muss zur domains-Allowlist des Keys passen
```

**Dataset finden — nicht raten:** Den exakten WDPA-Dataset-Namen und die Version
kennt die Spec nicht. Liste die Datasets selbst auf und trage die Werte in
`WDPA_DATASET` / `WDPA_VERSION` (oben in `gfw_data_api.py`, mit
`>>> an echtes WDPA-Dataset anpassen <<<` markiert) ein:

```python
from app.gfw_data_api import list_datasets
for d in list_datasets():
    print(d.get("dataset"))   # nach dem WDPA-Eintrag suchen
```

> ⚠️ **Namensverwechslung — wichtig:** „GFW Data API" = **Global *Forest* Watch**
> (Wald, Raster, Vektor, Schutzgebiete). Sie liefert **keine** Schiffspositionen.

---

## Schiffsdaten (AIS) — getrennte Quelle nötig

Die oben genannte Global **Forest** Watch Data API hat keinerlei Schiffs-,
AIS- oder Fishing-Effort-Endpunkte (alle 51 Pfade der Spec geprüft). **Schiffs­bewegungen
brauchen daher eine eigene, getrennte Datenquelle.** Mission Radar trennt das sauber:

| Frage | Quelle | Modul |
| ----- | ------ | ----- |
| Wo liegen Schutzgebiete? | WDPA (lokal **oder** GFW *Data* API) | [geo.py](backend/app/geo.py) / [gfw_data_api.py](backend/app/gfw_data_api.py) |
| Wo sind welche Schiffe? | AIS / Global *Fishing* Watch | [gfw_client.py](backend/app/gfw_client.py) |

Für echte Schiffsdaten kommen z. B. in Frage:

- **Global *Fishing* Watch APIs** (apparent fishing effort, Vessel/Events) —
  <https://globalfishingwatch.org/our-apis/documentation> — eine **andere** API als
  die Forest-Watch-Data-API.
- ein kommerzieller **AIS-Provider** (z. B. Satelliten-AIS).

Die Risk Engine bleibt von all dem unberührt: Beide Welten liefern am Ende fertige
`Vessel`-Objekte (inkl. des bereits berechneten `in_protected_area`-Flags).

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

---

## Validierung gegen bekannte IUU-Fälle

Ein erster, ehrlicher Qualitätsschritt: Hätte die Engine reale, dokumentierte
IUU-Fälle hoch bewertet? Das Validierungs-Framework liegt **getrennt** von der
Engine in [backend/validation/](backend/validation/) — es ruft die Engine nur auf,
ändert sie nie.

```bash
cd backend
.venv/bin/python -m validation.validate_scores
```

**Was passiert:** Bekannte Fälle aus
[known_cases.py](backend/validation/known_cases.py) (Thunder, Viking, Kunlun,
STS-50, Fu Yuan Yu Leng 999 + eine konstruierte Negativkontrolle) laufen durch
`rank_targets()`. Das Skript zeigt pro Fall Score, angeschlagene Regeln und die
Klassifikation (TP/FN/FP/TN) und am Ende eine ehrliche Auswertung mit
True-Positive-Rate, Schwächen und Empfehlungen.

**Aktuelles Ergebnis (Schwelle Score ≥ 50):**

| Kennzahl | Wert |
| --- | --- |
| Bekannte IUU-Fälle | 5 |
| Korrekt erkannt (TP) | 4 → **TP-Rate 80 %** |
| Nicht erkannt (FN) | 1 — *Fu Yuan Yu Leng 999* |
| Negativkontrolle | TN=1, FP=0 |

**Was die Ergebnisse bedeuten:** Die vier aktiv fischenden Poacher (AIS aus,
Fischerei-Tempo, im Sperrgebiet, Verweilen) werden klar erkannt. Der **Reefer**
*Fu Yuan Yu Leng 999* — 2017 mit ~6.600 Haien im Galápagos-Reservat gestellt —
rutscht durch: Er fischte nicht aktiv (kein Fischerei-Tempo) und hatte AIS an,
also greift nur die Schutzgebiets-Regel (+35). Das ist eine **echte, bewusst
gezeigte Schwäche** regelbasierter Speed-/Gap-Logik, kein Zufall.

> ⚠️ **Ehrlich:** Die Eingabewerte sind **synthetische Approximationen**
> dokumentierten Verhaltens, **keine** echten AIS-Traces. Jeder Fall trägt eine
> Quelle und ist als `approx` markiert. Eine kleine, handverlesene Fallzahl ist
> **keine** statistische Validierung.

**Nächste Schritte zur echten Validierung:**

1. **Echte AIS-Traces** der bekannten Fälle über den GFW-Token ziehen (Position,
   Speed, Gaps zum Tatzeitpunkt) statt sie zu schätzen.
2. **Größerer, unabhängig gelabelter Datensatz** mit Positiv- *und* Negativfällen
   (z. B. CCAMLR/Interpol-Listen vs. verifiziert legale Fahrzeuge).
3. **Schwelle und Gewichte kalibrieren** statt zu raten; Fehlerraten quantifizieren.
4. **Neue Regeln** für die gefundenen Lücken (Transshipment/Encounter,
   Identitäts-/Flaggenwechsel, Watchlist-Abgleich) — über die `RULES`-Liste,
   ohne die Engine umzubauen.
