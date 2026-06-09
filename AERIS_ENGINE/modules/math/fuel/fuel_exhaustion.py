from core.data_bus import DataBus


class FuelExhaustionModule:
    """FUEL-3 — Fuel Exhaustion Predictor.

    Computes time and distance to fuel exhaustion at current burn rate.
    Compares to distance remaining to destination.

    time_to_exhaustion_min = (fuel_total_lbs / fuel_flow_total_lbs_hr) × 60
    dist_reachable_nm = time_to_exhaustion_min / 60 × gs_kts

    WARN  fuel time remaining < 45 min  (ICAO final reserve threshold)
    CRIT  dist_reachable_nm < dist_to_dest_nm  (can't reach destination)
    """

    _SUPPRESS_PHASES = frozenset({"GROUND_ROLL", "ROTATION", "COMPLETE"})
    _WARN_MIN        = 45.0

    _ALERT_MAP = {
        "FUEL_EXHAUSTION_WARNING": {
            "id": "FUEL_EXHAUSTION", "severity": "warning",
            "msg": "FUEL BELOW FINAL RESERVE",
            "detail": "Fuel remaining < 45 min — final reserve breached, declare emergency",
        },
        "FUEL_EXHAUSTION_CRITICAL": {
            "id": "FUEL_EXHAUSTION", "severity": "critical",
            "msg": "CANNOT REACH DESTINATION",
            "detail": "Fuel range insufficient to reach destination — divert immediately",
        },
    }

    def __init__(self, ws=None, perf=None):
        self._ws = ws
        self._last_alert: str | None = None

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        phase = state.get("phase", "")
        if phase == "COMPLETE":
            await self._clear(); return
        if phase in self._SUPPRESS_PHASES:
            return

        fuel_lbs = state.get("fuel_total_lbs", 0.0)
        flow_lhr = state.get("fuel_flow_total_lbs_hr", 1.0)
        gs_kts   = state.get("gs_kts", state.get("ias_captain", 250.0))
        dist_nm  = state.get("dist_to_dest_nm", 0.0)

        if flow_lhr <= 0 or fuel_lbs <= 0:
            if self._last_alert:
                await self._clear()
            return

        time_to_exhaustion_min = (fuel_lbs / flow_lhr) * 60.0
        dist_reachable_nm      = (time_to_exhaustion_min / 60.0) * gs_kts

        state["fuel_time_remaining_min"] = round(time_to_exhaustion_min, 1)
        state["fuel_dist_reachable_nm"]  = round(dist_reachable_nm, 1)

        if dist_reachable_nm < dist_nm:
            alert = "FUEL_EXHAUSTION_CRITICAL"
        elif time_to_exhaustion_min < self._WARN_MIN:
            alert = "FUEL_EXHAUSTION_WARNING"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  fuel={fuel_lbs:.0f} lbs  time={time_to_exhaustion_min:.0f} min  reachable={dist_reachable_nm:.0f} nm  dest={dist_nm:.0f} nm")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] FUEL_EXHAUSTION CLEAR — sufficient fuel")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "FUEL_EXHAUSTION"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "FUEL_EXHAUSTION"})
