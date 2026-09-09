"""Diversion Decision Engine — Layer 3 DataBus subscriber.

Live wiring for GRACE (decision/diversion_selector.py). Runs right after
the Layer 2 assessment module so it can read state["assessment"] the same
tick it's written (see modules/registry.py for the ordering).

Implements the continuous-background-evaluation deployment model described
in the paper (Section V): maintains a standing shortlist of 3 candidates
per severity tier (LOW/MODERATE/HIGH -- 9 airports total) throughout the
whole flight, refreshed on a slow background cadence, not only once an
emergency is recognized. When assessment reports a real emergency, the
shortlist for that severity is re-verified -- a fresh NOTAM/fuel/turn check
against the real active conditions, over just those ~3 candidates rather
than the full airport database -- and the best surviving one is finalized.
If none of the 3 survive (e.g. all were just NOTAM-closed), it falls back
to a full-database rescan so a genuinely reachable airport is never missed.

GRACE itself doesn't know about the DataBus/AlertTracker/shortlist
scheme -- swapping the severity classifier later only means changing
_RISK_TO_SEVERITY here.
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
_SEVERITY_TIERS = ("LOW", "MODERATE", "HIGH")

_SUPPRESS_PHASES = frozenset({"GROUND_ROLL", "ROTATION", "LANDING", "COMPLETE"})
_SHORTLIST_REFRESH_INTERVAL_S = 15.0  # background refresh cadence, all three tiers


class DiversionDecisionModule:
    """Publishes state["diversion_shortlist"] every tick in flight (the
    standing 3-per-tier background shortlist) and state["diversion_recommendation"]
    whenever assessment reports an active emergency (overallRisk != "LOW")."""

    def __init__(self, perf=None, tracker=None):
        self._tracker = tracker
        self._is_transport = getattr(perf, "is_transport", True)
        self._vref_kts = getattr(perf, "vref_kts", 0.0)
        self._airports = load_airports()  # cached after first call in-process
        self._selector = DiversionSelector()

        self._shortlist: dict[str, list] = {tier: [] for tier in _SEVERITY_TIERS}
        self._last_shortlist_t = float("-inf")

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        if state.get("phase", "") in _SUPPRESS_PHASES:
            return

        assessment = state.get("assessment")
        if not assessment:
            return

        now = state.get("time", 0.0)

        # ── Standing background shortlist ───────────────────────────────
        # Refreshed for all three severity tiers on a slow cadence,
        # independent of whether an emergency is currently active. Always
        # computed with no active conditions -- it represents "reachable
        # candidates at this position/fuel state if severity were X",
        # not a hypothesis about which specific condition might occur.
        if now - self._last_shortlist_t >= _SHORTLIST_REFRESH_INTERVAL_S:
            for tier in _SEVERITY_TIERS:
                self._shortlist[tier] = self._selector.select(
                    state, self._airports,
                    is_transport=self._is_transport,
                    vref_kts=self._vref_kts,
                    severity=tier,
                    active_conditions=[],
                    top_n=3,
                )
            self._last_shortlist_t = now

        state["diversion_shortlist"] = dict(self._shortlist)

        severity = _RISK_TO_SEVERITY.get(assessment.get("overallRisk", "LOW"))
        if severity is None:
            state["diversion_recommendation"] = None
            return

        active_conditions = list(self._tracker.snapshot().keys()) if self._tracker else []

        # ── Final verification ──────────────────────────────────────────
        # Re-run GRACE restricted to this severity's standing shortlist,
        # with the real active conditions applied -- a fresh NOTAM/fuel/
        # turn check on ~3 candidates. Falls back to a full rescan only if
        # none of the 3 survive.
        shortlist_icaos = {c["icao"] for c in self._shortlist.get(severity, [])}
        pool = [a for a in self._airports if a["icao"] in shortlist_icaos] or self._airports

        candidates = self._selector.select(
            state, pool,
            is_transport=self._is_transport,
            vref_kts=self._vref_kts,
            severity=severity,
            active_conditions=active_conditions,
            top_n=3,
        )
        if not candidates and pool is not self._airports:
            candidates = self._selector.select(
                state, self._airports,
                is_transport=self._is_transport,
                vref_kts=self._vref_kts,
                severity=severity,
                active_conditions=active_conditions,
                top_n=3,
            )

        state["diversion_recommendation"] = {
            "severity":            severity,
            "activeConditions":    active_conditions,
            "candidates":          candidates,
            "degraded":            bool(candidates) and candidates[0].get("relaxed", False),
            "relaxationNotes":     candidates[0].get("relaxation_notes", []) if candidates else [],
            "noReachableAirport":  not candidates,
            "fromShortlist":       bool(shortlist_icaos),
        }
