from core.data_bus import DataBus


class TakeoffPerformanceModule:
    """PERF-3 — Takeoff Performance Monitor.

    Validates that the aircraft is accelerating as expected during takeoff roll.
    Uses a simplified acceleration check: by 50% of V1 speed, at least 30% of
    IAS should be achieved.  If acceleration is abnormally slow, an engine
    problem or contaminated runway may be the cause.

    Also monitors that V1 is reached within expected runway length proxy
    (throttle at 100% for > 20 ticks without reaching V1 = problem).

    WARN  acceleration significantly below expected (IAS lag)
    CRIT  V1 not reached and runway likely exhausted (throttle timer exceeded)
    """

    _ACTIVE_PHASE   = "GROUND_ROLL"
    _WARN_TICKS_MAX = 20
    _V1_FRACTION    = 0.50

    _ALERT_MAP = {
        "TKOF_PERF_WARN": {
            "id": "TKOF_PERF", "severity": "warning",
            "msg": "TAKEOFF PERFORMANCE LOW",
            "detail": "Aircraft accelerating below expected — check engine thrust and runway condition",
        },
        "TKOF_PERF_CRIT": {
            "id": "TKOF_PERF", "severity": "critical",
            "msg": "V1 NOT REACHED",
            "detail": "Prolonged takeoff roll without reaching V1 — reject takeoff if runway permits",
        },
    }

    def __init__(self, ws=None, perf=None):
        self._ws         = ws
        self._v1_kts     = (perf.v1_kts   if perf else 0.0) or 0.0
        self._vr_kts     = (perf.vr_kts   if perf else 0.0) or 0.0
        self._roll_ticks = 0
        self._last_alert: str | None = None

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        phase = state.get("phase", "")

        if phase == "COMPLETE":
            await self._clear(); return

        if phase != self._ACTIVE_PHASE:
            self._roll_ticks = 0
            if self._last_alert:
                await self._clear()
            return

        ias      = state.get("ias_captain", 0.0)
        throttle = state.get("throttle_pct", 0.0)

        if throttle > 90.0:
            self._roll_ticks += 1

        v1 = self._v1_kts if self._v1_kts > 0 else 120.0

        if ias >= v1:
            if self._last_alert:
                await self._clear()
            return

        if self._roll_ticks > self._WARN_TICKS_MAX * 2:
            alert = "TKOF_PERF_CRIT"
        elif self._roll_ticks > self._WARN_TICKS_MAX and ias < v1 * self._V1_FRACTION:
            alert = "TKOF_PERF_WARN"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  IAS={ias:.0f}  V1={v1:.0f}  roll_ticks={self._roll_ticks}  throttle={throttle:.0f}%")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] TKOF_PERF CLEAR — acceleration normal")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "TKOF_PERF"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._roll_ticks = 0
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "TKOF_PERF"})
