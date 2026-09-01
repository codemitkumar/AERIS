"""Diversion Decision Engine — Layer 3 DataBus subscriber.

Live wiring for GRACE (decision/diversion_selector.py). Runs right after
the Layer 2 assessment module so it can read state["assessment"] the same
tick it's written (see modules/registry.py for the ordering).

Turns "assessment says X" into "here are the top 3 diversion airports" —
GRACE itself doesn't know about the DataBus/AlertTracker, so swapping the
severity classifier later only means changing _RISK_TO_SEVERITY here.
"""

from core.data_bus import DataBus
from data.ingestion.faa_airport_loader import load_airports
from decision.diversion_selector import DiversionSelector

# overallRisk (4 tiers, LOW = nothing active) -> our severity (3 tiers).
_RISK_TO_SEVERITY = {
    "LOW":      None,
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
            "degraded":         bool(candidates) and candidates[0].get("relaxed", False),
            "relaxationNotes":  candidates[0].get("relaxation_notes", []) if candidates else [],
            "noReachableAirport": not candidates,
        }
