# Mission Radar — Vision & Architektur

*Eine maritime Intelligence-Plattform für den Meeresschutz*

> **Status:** Vision- und Architekturdokument. Beschreibt das Zielbild und die
> bewusst gezogenen Grenzen. Nicht alles hier ist gebaut — das Dokument trennt
> klar zwischen dem, was heute läuft, was als Nächstes kommt, und was bewusst
> *nicht* automatisiert wird.

---

## 1. Vision in einem Satz

Eine Plattform, die aus öffentlichen maritimen Daten ein verständliches Lagebild
erzeugt und Meeresschutzorganisationen hilft, illegale Fischerei,
Schutzgebietsverletzungen und andere Bedrohungen früher zu erkennen — als
*Werkzeug für menschliche Analysten*, nicht als automatische Verdachtsmaschine.

---

## 2. Das Problem

Täglich bewegen sich Hunderttausende Schiffe auf den Weltmeeren. Für eine NGO ist
es praktisch unmöglich, alle zu beobachten, ihre Historie zu verstehen und
verdächtige Aktivität rechtzeitig herauszufiltern. Die relevanten Informationen
sind über viele Quellen verteilt — AIS, Satellit, Schiffsregister, offizielle
Listen. Das Ergebnis ist nicht zu wenig Information, sondern zu viel, ohne
Priorisierung.

---

## 3. Das Leitprinzip: zwei Zonen, eine klare Grenze

Dies ist die wichtigste Designentscheidung der ganzen Plattform. Sie entscheidet
darüber, ob eine echte Organisation das Tool anfassen kann oder ob ihre
Rechtsabteilung es sofort stoppt.

### Zone A — Maritime Verhaltensanalyse (voll automatisierbar)

Hier arbeitet das System mit **öffentlichen Daten über Schiffe als Objekte auf
öffentlichen Gewässern**. Das ist datentechnisch sauber, rechtlich tragfähig und
bildet den Kern der Plattform. Alles in dieser Zone darf das System automatisch
bewerten, scoren und als Warnung ausspielen:

- Schiffspositionen, Bewegungsmuster, Geschwindigkeitsprofile
- AIS-Lücken, Loitering, Rendezvous-Muster
- Aufenthalt in oder nahe Schutzgebieten
- Flaggenhistorie, frühere Schiffsnamen, frühere MMSI (öffentliche Registerdaten)
- Abgleich mit **offiziellen** Listen (FAO IUU-Liste, RFMO-Schwarzlisten,
  offizielle Sanktionslisten) — das sind autoritative, veröffentlichte Quellen

### Zone B — Akteure, Eigentümer, Ermittlung (menschengeführt, nicht automatisiert)

Sobald es um **Personen, Eigentümer und die Zuweisung von Verdacht** geht, ändert
sich die Natur des Systems. Hier gilt eine harte Regel:

> **Das System erzeugt keinen automatischen Verdacht über Personen oder private
> Akteure. Es stellt Informationen für ausgebildete Analysten bereit, die mit
> Sorgfaltspflicht, Quellennachweis und Korrekturmöglichkeit arbeiten.**

Warum diese Grenze nicht verhandelbar ist:

- **Rechtlich:** Das automatisierte Verknüpfen von Privatpersonen mit
  Verdachtsmomenten berührt Datenschutz (DSGVO bei EU-Bezug), Persönlichkeitsrechte
  und potenziell Verleumdung. Ein falsch markierter Mensch kann realen Schaden
  erleiden — und das Projekt rechtlich exponieren.
- **Operativ:** Die seriösen Akteure im Feld (GFW, TMT, SkyTruth) trennen genau so.
  Sie veröffentlichen Schiffsdaten, betreiben aber keine Maschine, die eigenständig
  ableitet, welche *Person* verdächtig ist. Diese Zuordnung machen Analysten.
- **Ethisch:** Ein System, das automatisch "verdächtige Eigentümerstrukturen"
  ausgibt, produziert mit hoher Sicherheit Fehlbeschuldigungen. Bei Schiffen ist
  ein False Positive ein vergeudeter Patrouillen-Tag. Bei Personen ist es
  möglicherweise ein zerstörter Ruf.

In Zone B ist die Plattform also ein **Aktenkoffer für Analysten**: Sie hält fest,
was ein Mensch recherchiert und entschieden hat. Sie entscheidet nicht selbst.

---

## 4. Was die Plattform leisten soll

### 4.1 Globale maritime Übersicht *(Zone A)*

Eine interaktive Karte mit Schiffen, Hotspots, Schutzgebieten und Risikozonen.
Nutzer wählen Regionen frei und legen eigene Arbeitsbereiche an. Pro Abruf ein
begrenzter Ausschnitt (technische Grenze von AIS-Datenmengen), aber jede Region
der Welt erreichbar.

### 4.2 Schiffsprofile *(Zone A, mit klarer Grenze zu Zone B)*

Jedes Schiff bekommt ein Profil aus **öffentlichen Schiffsdaten**: aktuelle und
historische Bewegungen, Risikobewertung aus dem Verhaltensmodell, Flaggenhistorie,
frühere Namen, Aufenthalte in Schutzgebieten, Treffer auf offiziellen Listen.

Eigentümer-/Betreiberinformationen werden *angezeigt, wenn sie aus offiziellen
Registern stammen*, aber nicht vom System zu Verdachts-Scores über Personen
verrechnet. Das ist die Profil-Grenze zwischen Zone A und B.

### 4.3 Verhaltensanalyse & Anomalie-Erkennung *(Zone A)*

Kontinuierliche Analyse von Bewegungsmustern, AIS-Ausfällen, ungewöhnlichen Routen
und Aktivität in Schutzgebieten. Langfristig lernen Modelle, verdächtige
*Verhaltensmuster von Schiffen* zu erkennen. Wichtig: Das Modell bewertet
Verhalten, nicht Menschen.

### 4.4 Offizielle-Listen-Integration *(Zone A)*

Abgleich mit autoritativen, veröffentlichten Quellen: FAO IUU-Liste,
RFMO-Schwarzlisten, offizielle Sanktionslisten. Das sind Fakten aus offiziellen
Stellen — kein vom System abgeleiteter Verdacht.

### 4.5 Ermittlungs- & Notizsystem *(Zone B — menschengeführt)*

Analysten können Notizen schreiben, Fälle anlegen, Dokumente und Beweise sammeln
und Erkenntnisse teilen. Das System speichert und strukturiert die Arbeit von
Menschen. Jeder Verdacht in diesem Modul ist als *menschliche Einschätzung mit
Quelle* gekennzeichnet, niemals als Systemurteil. Korrektur und Löschung müssen
jederzeit möglich sein.

### 4.6 Automatische Warnungen *(nur Zone A)*

Das System erzeugt automatische Hinweise — aber ausschließlich über
*Schiffsereignisse*: Schutzgebietsverletzungen, ungewöhnliche Verhaltensmuster,
Auftauchen eines Schiffs von einer offiziellen Liste. Keine automatischen
Warnungen über Personen oder abgeleitete "verdächtige Strukturen".

### 4.7 Netzwerkanalyse *(eingeschränkt, überwiegend Zone B)*

Beziehungen zwischen **Schiffen, Häfen und Ereignissen** dürfen visualisiert
werden — das sind operative, sachliche Verbindungen. Netzwerke, die *Personen* als
Knoten haben und Verdacht über sie nahelegen, gehören in das menschengeführte
Ermittlungsmodul und werden nicht automatisch als "aufgedecktes Netzwerk"
präsentiert.

---

## 5. System-Architektur

```
┌──────────────────────────────────────────────────────────────┐
│                        FRONTEND                              │
│   Karte · Schiffsprofile · Warnungen · Analysten-Workspace   │
└───────────────────────────┬──────────────────────────────────┘
                            │  REST API
┌───────────────────────────┴──────────────────────────────────┐
│                      APPLICATION LAYER                       │
│                                                              │
│   ┌────────────────────┐      ┌─────────────────────────┐    │
│   │   ZONE A (auto)    │      │  ZONE B (human-led)     │    │
│   │                    │      │                         │    │
│   │  Risk Engine       │      │  Case / Notes Store     │    │
│   │  Pattern Engine    │      │  Analyst Annotations    │    │
│   │  Anomaly Detection │      │  Document Vault         │    │
│   │  Alert Generator   │      │  (no auto-suspicion)    │    │
│   └─────────┬──────────┘      └────────────┬────────────┘    │
│             │                              │                 │
│   ┌─────────┴──────────────────────────────┴────────────┐    │
│   │             Vessel / Geo Data Model                  │    │
│   │   (austauschbare Datenquellen, stabile Engine)       │    │
│   └─────────┬────────────────────────────────────────────┘    │
└─────────────┼─────────────────────────────────────────────────┘
              │
┌─────────────┴─────────────────────────────────────────────────┐
│                       DATA SOURCES                            │
│                                                               │
│   AIS / Events (GFW API v3)   ·   Schutzgebiete (WDPA)        │
│   Offizielle Listen (FAO, RFMO, Sanktionen)                   │
│   Schiffsregister (öffentliche Flaggen-/Namenshistorie)       │
│                                                               │
│   [Zone B reichert NUR durch menschliche Analysten an,        │
│    nicht durch automatisches Scraping von Personen-Daten]     │
└───────────────────────────────────────────────────────────────┘
```

**Architektur-Grundsatz, der sich durchzieht:** Die Datenquelle ist austauschbar,
die Engine stabil. Was heute synthetische Daten sind, wird morgen GFW, ohne dass
die Bewertungslogik sich ändert. Diese Trennung ist bereits im aktuellen Code
umgesetzt.

---

## 6. Entwicklungsphasen

| Phase | Inhalt | Zone | Status |
|-------|--------|------|--------|
| 1 | Regelbasierte Risk Engine, Karte, Top-Targets | A | **fertig** |
| 2 | Echte GFW-Daten, Schutzgebiete, dynamische Region, Score-Kalibrierung | A | **in Arbeit** |
| 3 | Mustererkennung, Anomalie-Modelle, Hotspot-Intelligence | A | geplant |
| 4 | Schiffsprofile mit öffentlicher Historie, offizielle-Listen-Abgleich | A | geplant |
| 5 | Analysten-Workspace: Fälle, Notizen, Dokumente | B (human-led) | geplant |
| 6 | Automatische Schiffs-Warnungen, geteilte Arbeitsbereiche | A | Vision |

Bewusst **nicht** auf der Roadmap als automatisierte Funktion: Personen-Scoring,
abgeleitete Verdachts-Netzwerke über Privatpersonen, automatisches OSINT-Scraping
über Individuen. Diese Dinge können nur als menschengeführte, quellenbelegte
Analystenarbeit existieren — und nur dort, wo eine Organisation mit der nötigen
rechtlichen Absicherung dahintersteht.

---

## 7. Verantwortung & Grenzen (der Abschnitt, der das Projekt seriös macht)

- **Schiffe ≠ Personen.** Das System bewertet Schiffsverhalten automatisch,
  menschliche Akteure nie.
- **Offizielle Listen ≠ eigener Verdacht.** Ein Treffer auf einer FAO- oder
  Sanktionsliste ist ein Fakt einer Behörde. Ein vom System abgeleiteter Verdacht
  ist eine Hypothese und wird auch so gekennzeichnet.
- **Scores sind Hypothesen, keine Beweise.** Jede Risikobewertung kommt mit
  Begründung und Confidence-Level. Ein hoher Score ist ein Hinweis zum
  *Hinschauen*, keine Feststellung von Schuld.
- **False Positives haben Folgen.** Bei Schiffen ein vergeudeter Einsatztag, bei
  Personen ein möglicher Schaden. Deshalb die harte Zonen-Grenze.
- **Korrigierbarkeit.** Alles im Ermittlungsmodul muss editier- und löschbar sein.
  Menschen machen Fehler; das System muss sie zurücknehmen können.
- **Datensparsamkeit.** Über Personen wird nur gespeichert, was ein Analyst aktiv
  und begründet einträgt — kein automatisches Anhäufen.

---

## 8. Warum diese Abgrenzung die Vision *stärkt*

Es wäre verlockend, alles automatisch zu machen — die "Verdachtsmaschine über
alle Akteure" klingt mächtig. Aber genau dieser Teil ist es, der eine echte
Organisation abschrecken und das Projekt rechtlich gefährden würde. Die Plattform
wird *dadurch* einsetzbar, dass sie die mächtige automatische Analyse auf den
sauberen Bereich (Schiffe, öffentliche Daten, offizielle Listen) konzentriert und
den sensiblen Bereich (Personen, Verdacht) dem menschlichen Urteil mit
Sorgfaltspflicht überlässt.

Das ist der Unterschied zwischen einem beeindruckenden Demo und einem System, mit
dem eine NGO tatsächlich arbeiten würde.
