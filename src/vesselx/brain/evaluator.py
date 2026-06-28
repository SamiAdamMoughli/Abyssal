"""Rule evaluation engine — applies the VesselX rulebook to vessel snapshots.

This module is intentionally stateless and side-effect-free. Given a vessel
state dict it returns every triggered AlertFinding. Persistence and WebSocket
broadcast are the caller's responsibility (brain.tasks).

Zone A boundary (from project rules): the evaluator only receives kinematic
and spatial signals. It never touches person-level data, ownership graphs, or
analyst notes. The Zone B boundary is enforced structurally — those fields
simply don't exist in the vessel_state schema this module operates on.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from vesselx.brain.rules import RULES, Severity


@dataclass(frozen=True)
class AlertFinding:
    """One triggered rule result from a single vessel evaluation pass."""

    alert_id:    str
    rule_id:     str
    rule_label:  str
    severity:    Severity
    message:     str
    mmsi:        str | None
    lat:         float | None
    lon:         float | None
    h3_index:    str | None
    triggered_at: datetime

    def as_dict(self) -> dict:
        return {
            "alert_id":     self.alert_id,
            "rule_id":      self.rule_id,
            "rule_label":   self.rule_label,
            "severity":     self.severity.value,
            "message":      self.message,
            "mmsi":         self.mmsi,
            "lat":          self.lat,
            "lon":          self.lon,
            "h3_index":     self.h3_index,
            "triggered_at": self.triggered_at.isoformat(),
        }


def evaluate(vessel_state: dict) -> list[AlertFinding]:
    """Run every registered rule against ``vessel_state``.

    Args:
        vessel_state: Dict with vessel fields. Must contain at minimum
                      ``mmsi``, ``lat``, ``lon``. Additional risk fields
                      (in_protected_area, behavior_status, risk_score, etc.)
                      are used where present; missing keys default to falsy
                      within each predicate lambda.

    Returns:
        List of AlertFinding for each triggered rule. Empty list = vessel
        is currently clean against the full rulebook.
    """
    findings: list[AlertFinding] = []
    now = datetime.now(timezone.utc)

    for rule in RULES:
        try:
            triggered = rule.predicate(vessel_state)
        except Exception:
            triggered = False

        if not triggered:
            continue

        try:
            msg = rule.message(vessel_state)
        except Exception:
            msg = f"{rule.label} triggered for mmsi={vessel_state.get('mmsi')}"

        findings.append(AlertFinding(
            alert_id=str(uuid4()),
            rule_id=rule.id,
            rule_label=rule.label,
            severity=rule.severity,
            message=msg,
            mmsi=vessel_state.get("mmsi"),
            lat=vessel_state.get("lat"),
            lon=vessel_state.get("lon"),
            h3_index=vessel_state.get("h3_index"),
            triggered_at=now,
        ))

    return findings


def evaluate_batch(vessel_states: list[dict]) -> dict[str, list[AlertFinding]]:
    """Evaluate a batch of vessel states; returns dict keyed by mmsi."""
    return {
        state.get("mmsi", ""): evaluate(state)
        for state in vessel_states
    }
