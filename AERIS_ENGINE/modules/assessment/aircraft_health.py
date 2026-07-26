"""Aircraft Situation Assessment — Layer 2 aggregator.

Runs as the LAST DataBus subscriber every tick.  By the time this module's
on_state() executes, all 53 math modules have already processed the same tick
and updated the AlertTracker.  This module reads the live alert snapshot,
groups alerts by subsystem, computes a health score for each, and writes a
single structured 'assessment' key into the state dict.

The assessment is what the AI trains on: meaningful subsystem health signals
rather than 50+ raw sensor numbers.

Output written to state["assessment"]:
{
    "fuel":          {"health": 0.5, "severity": "warning", "activeIssues": ["FUEL LEAK"]},
    "engine":        {"health": 1.0, "severity": "normal",  "activeIssues": []},
    ...
    "overallRisk":   "HIGH",
    "activeAlertCount": 3,
    "criticalCount": 1
}

Health score:
    1.0             — no active alerts in subsystem
    -0.25 per WARNING alert (min 0.0)
    -0.50 per CRITICAL alert (min 0.0)

Overall risk:
    CRITICAL — any critical alert active
    HIGH     — min subsystem health < 0.5  OR  total active alerts >= 4
    MODERATE — any warning active
    LOW      — nothing active
"""

from core.data_bus import DataBus

_SUBSYSTEMS = [
    "altimetry", "speed", "glide", "engine",
    "attitude", "pressurization", "gpws",
    "approach", "icing", "fuel", "performance",
]

_ALERT_SUBSYSTEM: dict[str, str] = {
    # Altimetry
    "ALT_DISAGREE":    "altimetry",
    "UNCOMMANDED_DES": "altimetry",
    "RAPID_ALT_LOSS":  "altimetry",
    "ENERGY_STATE":    "altimetry",
    "G_LIMIT":         "altimetry",
    "NEG_G":           "altimetry",
    "ALT_JERK":        "altimetry",
    "BARO_SET":        "altimetry",
    # Speed
    "CAPT_ADC":        "speed",
    "FO_ADC":          "speed",
    "UNRELIABLE_SPD":  "speed",
    "OVERSPEED":       "speed",
    "STALL":           "speed",
    "LOW_SPEED":       "speed",
    "FLAP_OVERSPEED":  "speed",
    "GEAR_OVERSPEED":  "speed",
    "VA_OVERSPEED":    "speed",
    # Glide
    "GLIDE_RANGE":     "glide",
    "BEST_GLIDE":      "glide",
    "EMRG_DESCENT":    "glide",
    "GLIDESLOPE":      "glide",
    "VREF":            "glide",
    # Engine
    "N1_DISAGREE":     "engine",
    "THRUST_ASYM":     "engine",
    "EGT_OVERTEMP":    "engine",
    "ENGINE_FAILURE":  "engine",
    "ENG_EFF":         "engine",
    "COMP_STALL":      "engine",
    # Attitude
    "UNUSUAL_ATT":     "attitude",
    "BANK_ANGLE":      "attitude",
    "PITCH_LIMIT":     "attitude",
    "G_LOAD":          "attitude",
    "SIDESLIP":        "attitude",
    # Pressurization
    "CABIN_ALT":       "pressurization",
    "RAPID_DECOMP":    "pressurization",
    "DIFF_PRESS":      "pressurization",
    # GPWS
    "SINK_RATE":       "gpws",
    "TERRAIN_CLOSURE": "gpws",
    "ALT_LOSS_TKOF":   "gpws",
    "UNSAFE_CONFIG":   "gpws",
    "GPWS_GLIDESLOPE": "gpws",
    # Approach
    "UNSTABLE_APPR":   "approach",
    "LOW_ENERGY":      "approach",
    "CROSSWIND":       "approach",
    "TAILWIND":        "approach",
    "GO_AROUND":       "approach",
    # Icing
    "ICING_COND":      "icing",
    "ANTI_ICE_OFF":    "icing",
    "ICE_ACCUM":       "icing",
    # Fuel
    "FUEL_LEAK":       "fuel",
    "FUEL_IMBALANCE":  "fuel",
    "FUEL_EXHAUSTION": "fuel",
    "MIN_DIVERT_FUEL": "fuel",
    "FUEL_EFF":        "fuel",
    "ENDURANCE":       "fuel",
    # Performance
    "DENSITY_ALT":     "performance",
    "TKOF_PERF":       "performance",
    "CRUISE_ALT_DEV":  "performance",
}

_WARN_PENALTY = 0.25
_CRIT_PENALTY = 0.50


class AircraftHealthModule:
    """Aggregates all active alerts into a structured health snapshot.

    Must be registered LAST so every math module has already processed the
    current tick and updated the AlertTracker before this runs.
    """

    def __init__(self, tracker=None):
        self._tracker = tracker

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        if self._tracker is None:
            return

        alerts = self._tracker.snapshot()

        # Group by subsystem
        by_subsystem: dict[str, list[dict]] = {s: [] for s in _SUBSYSTEMS}
        for aid, info in alerts.items():
            sub = _ALERT_SUBSYSTEM.get(aid, "performance")
            by_subsystem[sub].append({"id": aid, **info})

        # Compute per-subsystem health
        result: dict = {}
        min_health    = 1.0
        total_alerts  = 0
        critical_count = 0

        for sub in _SUBSYSTEMS:
            active = by_subsystem[sub]
            health = 1.0
            worst  = "normal"

            for a in active:
                sev = a.get("severity", "warning")
                if sev == "critical":
                    health -= _CRIT_PENALTY
                    worst   = "critical"
                    critical_count += 1
                else:
                    health -= _WARN_PENALTY
                    if worst != "critical":
                        worst = "warning"

            health = round(max(0.0, health), 2)
            min_health = min(min_health, health)
            total_alerts += len(active)

            result[sub] = {
                "health":       health,
                "severity":     worst,
                "activeIssues": [a["msg"] for a in active],
            }

        # Overall risk
        if critical_count > 0:
            overall = "CRITICAL"
        elif min_health < 0.5 or total_alerts >= 4:
            overall = "HIGH"
        elif total_alerts > 0:
            overall = "MODERATE"
        else:
            overall = "LOW"

        result["overallRisk"]       = overall
        result["activeAlertCount"]  = total_alerts
        result["criticalCount"]     = critical_count

        state["assessment"] = result
