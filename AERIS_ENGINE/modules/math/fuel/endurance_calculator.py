from core.data_bus import DataBus


class EnduranceCalculatorModule:
    """FUEL-6 — Endurance Calculator.

    Computes and publishes remaining flight endurance (hours and minutes)
    at current fuel flow rate.  Also tracks whether holding fuel is available.

    endurance_hr = fuel_total_lbs / fuel_flow_total_lbs_hr

    WARN  endurance_hr < 1.0  (less than one hour of fuel)
    CRIT  endurance_hr < 0.5  (30 minutes — final reserve territory)

    This module focuses on time-based endurance vs fuel_exhaustion which
    focuses on distance; both are published into state for AI module use.
    """

    _SUPPRESS_PHASES = frozenset({"GROUND_ROLL", "ROTATION", "COMPLETE"})
    _WARN_HR         = 1.0
    _CRIT_HR         = 0.5

    _ALERT_MAP = {
        "ENDURANCE_WARNING": {
            "id": "ENDURANCE", "severity": "warning",
            "msg": "ENDURANCE < 1 HOUR",
            "detail": "Less than 1 hour fuel remaining — plan landing or diversion now",
        },
        "ENDURANCE_CRITICAL": {
            "id": "ENDURANCE", "severity": "critical",
            "msg": "ENDURANCE < 30 MINUTES",
            "detail": "30 minutes fuel remaining — declare emergency, land immediately",
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

        if flow_lhr <= 0 or fuel_lbs <= 0:
            if self._last_alert:
                await self._clear()
            return

        endurance_hr  = fuel_lbs / flow_lhr
        endurance_min = endurance_hr * 60.0

        state["endurance_hr"]  = round(endurance_hr,  2)
        state["endurance_min"] = round(endurance_min, 0)

        if endurance_hr < self._CRIT_HR:
            alert = "ENDURANCE_CRITICAL"
        elif endurance_hr < self._WARN_HR:
            alert = "ENDURANCE_WARNING"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  fuel={fuel_lbs:.0f} lbs  endurance={endurance_min:.0f} min  phase={phase}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] ENDURANCE CLEAR — sufficient endurance")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "ENDURANCE"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "ENDURANCE"})
