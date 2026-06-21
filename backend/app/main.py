"""Mission Radar - FastAPI Schicht.

Bewusst duenn: Diese Datei haelt keine Fachlogik. Sie holt Schiffe aus der
(austauschbaren) Datenquelle, laesst die (feste) Engine bewerten und
serialisiert das Ergebnis. Saemtliche Bewertungslogik lebt in risk_engine.py.

Start (aus dem Ordner backend/):
    uvicorn app.main:app --reload
"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import risk_engine
from .risk_engine import RiskReason, TargetAssessment
from .sample_data import PROTECTED_AREA_CENTER, get_source

app = FastAPI(
    title="Mission Radar API",
    description=(
        "Decision-Support fuer die Bekaempfung illegaler Fischerei. "
        "Priorisiert Ziele und liefert IMMER eine Begruendung."
    ),
    version="0.1.0",
)

# CORS fuer das lokale Frontend offen (Phase 1). Fuer einen echten Einsatz
# muessen die erlaubten Origins eingeschraenkt werden.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

# Die Datenquelle wird einmal aufgeloest. In Phase 2 aendert sich nur, was
# get_source() zurueckgibt - dieser Code bleibt gleich.
_source = get_source()


# --------------------------------------------------------------------------- #
# Serialisierung
# --------------------------------------------------------------------------- #


def _reason_to_dict(reason: RiskReason) -> Dict[str, Any]:
    return {
        "points": reason.points,
        "label": reason.label,
        "detail": reason.detail,
    }


def _assessment_to_dict(a: TargetAssessment) -> Dict[str, Any]:
    """Ein bewertetes Schiff als JSON inkl. Score, top_reason und reasons."""
    top = a.top_reason
    return {
        "mmsi": a.vessel.mmsi,
        "name": a.vessel.name,
        "lat": a.vessel.lat,
        "lon": a.vessel.lon,
        "speed_knots": a.vessel.speed_knots,
        "in_protected_area": a.vessel.in_protected_area,
        "ais_gap_hours": a.vessel.ais_gap_hours,
        "flag": a.vessel.flag,
        "loitering_hours": a.vessel.loitering_hours,
        "score": a.score,
        "top_reason": _reason_to_dict(top) if top else None,
        "reasons": [_reason_to_dict(r) for r in a.reasons],
    }


# --------------------------------------------------------------------------- #
# Endpunkte
# --------------------------------------------------------------------------- #


@app.get("/")
def health() -> Dict[str, Any]:
    """Health-Check."""
    return {
        "status": "ok",
        "service": "Mission Radar API",
        "version": app.version,
        "rules_loaded": len(risk_engine.RULES),
    }


@app.get("/api/targets")
def get_targets(top_n: int = 5) -> Dict[str, Any]:
    """Top-N Ziele mit Begruendung - gerankt nach Score absteigend."""
    vessels = _source.get_vessels()
    ranked = risk_engine.rank_targets(vessels, top_n=top_n)
    return {
        "count": len(ranked),
        "targets": [_assessment_to_dict(a) for a in ranked],
    }


@app.get("/api/vessels")
def get_vessels() -> Dict[str, Any]:
    """Alle Schiffe mit Score - fuer die Kartendarstellung."""
    vessels = _source.get_vessels()
    assessments = risk_engine.assess_all(vessels)
    return {
        "count": len(assessments),
        "protected_area_center": PROTECTED_AREA_CENTER,
        "vessels": [_assessment_to_dict(a) for a in assessments],
    }
