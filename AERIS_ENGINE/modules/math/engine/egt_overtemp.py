from core.data_bus import DataBus


class EGTOvertempModule:
    """ENG-2 — EGT Overtemperature Monitor.

    Compares exhaust gas temperature to certified limits by flight phase.
    Exceeding limits causes turbine blade damage and eventual engine failure.

    Limits (from perf):
      TOGA climb:  egt_limit_toga_c
      Climb:       egt_limit_climb_c
      Cruise/other: egt_limit_cruise_c

    WARN  EGT > limit − 25 °C   (approaching limit)
    CRIT  EGT > limit            (limit exceeded — reduce thrust)
    """

    _SUPPRESS_PHASES = frozenset({"GROUND_ROLL", "COMPLETE"})

    _ALERT_MAP = {
        "EGT_APPROACH_LIMIT": {
            "id": "EGT_OVERTEMP", "severity": "warning",
            "msg": "EGT APPROACHING LIMIT",
            "detail": "Exhaust temperature within 25 °C of limit — monitor closely",
        },
        "EGT_EXCEEDED": {
            "id": "EGT_OVERTEMP", "severity": "critical",
            "msg": "EGT LIMIT EXCEEDED",
            "detail": "EGT above certified limit — reduce thrust immediately",
        },
    }

    def __init__(self, ws=None, perf=None):
        self._ws    = ws
        self._perf  = perf
        self._last_alert: str | None = None

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        phase = state.get("phase", "")
        if phase == "COMPLETE":
            await self._clear(); return
        if phase in self._SUPPRESS_PHASES:
            return

        egt_list = state.get("egt_c", [])
        if not egt_list:
            return

        if self._perf:
            if phase in ("ROTATION", "CLIMB") and state.get("throttle_pct", 0) > 90:
                limit = self._perf.egt_limit_toga_c
            elif phase == "CLIMB":
                limit = self._perf.egt_limit_climb_c
            else:
                limit = self._perf.egt_limit_cruise_c
        else:
            limit = 900.0

        worst_egt = max(egt_list)

        if worst_egt > limit:
            alert = "EGT_EXCEEDED"
        elif worst_egt > limit - 25.0:
            alert = "EGT_APPROACH_LIMIT"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  EGT={worst_egt:.0f}°C  limit={limit:.0f}°C  Δ={worst_egt-limit:+.0f}°C  phase={phase}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] EGT_OVERTEMP CLEAR — within limits")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "EGT_OVERTEMP"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "EGT_OVERTEMP"})
