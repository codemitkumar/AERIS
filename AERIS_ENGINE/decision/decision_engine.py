"""Diversion Decision Engine — Layer 3 DataBus subscriber.

Runs after modules/assessment/aircraft_health.py (Layer 2) in the same tick,
so it can read the fresh state["assessment"]["overallRisk"] the moment that
module writes it (DataBus subscribers registered without internal `await`
points run to completion in registration order — see modules/registry.py).

This module owns exactly one job: turn "assessment says X" into "here are
the top 3 diversion airports right now", by calling into
decision.diversion_selector — a plug-and-play scoring engine that knows
nothing about the DataBus, AlertTracker, or FlightGenerator. Swapping the
severity classifier for a real AI model later means changing
_RISK_TO_SEVERITY (or replacing this module's on_state entirely) — the
selector itself needs no changes.
"""

from core.data_bus import DataBus
from data.ingestion.faa_airport_loader import load_airports
from decision.diversion_selector import DiversionSelector

# assessment.overallRisk (4 tiers, "LOW" = nothing active) -> this module's
# emergency severity (3 tiers, only assigned once something IS active).
_RISK_TO_SEVERITY = {
    "LOW":      None,        # nothing active — no diversion recommendation needed
    "MODERATE": "LOW",
    "HIGH":     "MODERATE",
    "CRITICAL": "HIGH",
}

_SUPPRESS_PHASES = frozenset({"GROUND_ROLL", "ROTATION", "LANDING", "COMPLETE"})
_RECOMPUTE_INTERVAL_S = 15.0  # sim-time seconds between full airport rescans while elevated


class DiversionDecisionModule:
    """Publishes state["diversion_recommendation"] whenever assessment
    reports an active emergency (overallRisk != "LOW")."""

    def __init__(self, perf=None, tracker=None):
        self._tracker = tracker
        self._is_transport = getattr(perf, "is_transport", True)
        self._vref_kts = getattr(perf, "vref_kts", 0.0)
        self._airports = load_airports()  # cached after first call in-process
        self._selector = DiversionSelector()

        self._last_severity = None
        self._last_compute_t = float("-inf")
        self._last_result: list | None = None

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        if state.get("phase", "") in _SUPPRESS_PHASES:
            return

        assessment = state.get("assessment")
        if not assessment:
            return

        severity = _RISK_TO_SEVERITY.get(assessment.get("overallRisk", "LOW"))
        if severity is None:
            self._last_severity = None
            self._last_result = None
            return

        now = state.get("time", 0.0)
        active_conditions = list(self._tracker.snapshot().keys()) if self._tracker else []

        recompute = (
            severity != self._last_severity or
            self._last_result is None or
            now - self._last_compute_t >= _RECOMPUTE_INTERVAL_S
        )
        if recompute:
            self._last_result = self._selector.select(
                state, self._airports,
                is_transport=self._is_transport,
                vref_kts=self._vref_kts,
                severity=severity,
                active_conditions=active_conditions,
                top_n=3,
            )
            self._last_severity = severity
            self._last_compute_t = now

        candidates = self._last_result or []
        state["diversion_recommendation"] = {
            "severity":         severity,
            "activeConditions": active_conditions,
            "candidates":       candidates,
            # True once any candidate needed loosened fuel/turn/runway margins
            # to be found at all — see decision/diversion_selector.py's
            # progressive relaxation. False (and notes empty) when the
            # top pick was found at full strictness.
            "degraded":         bool(candidates) and candidates[0].get("relaxed", False),
            "relaxationNotes":  candidates[0].get("relaxation_notes", []) if candidates else [],
            "noReachableAirport": not candidates,
        }
