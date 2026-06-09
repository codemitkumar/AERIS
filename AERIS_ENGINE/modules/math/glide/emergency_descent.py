from core.data_bus import DataBus


class EmergencyDescentModule:
    """GLI-3 — Emergency Descent Profile Monitor.

    Detects when an aircraft at cruise altitude is descending at idle power
    (emergency descent scenario) and verifies the descent rate is adequate
    to clear the cabin as quickly as possible.

    Optimal emergency descent: 250-320 kts IAS, VS > 2 500 ft/min, idle thrust.
    A descent that is too shallow wastes time at altitude and prolongs hypoxia risk.

    Active when: phase=CRUISE or CLIMB, throttle < 5 %, alt > 10 000 ft.

    WARN  VS > −1 500 ft/min  (descending but not quickly enough)
    CRIT  VS > −800 ft/min    (nearly level — emergency not being executed)
    """

    _ACTIVE_PHASES   = frozenset({"CRUISE", "CLIMB"})
    _IDLE_THRESH     = 5.0
    _MIN_ALT_FT      = 10_000.0
    _WARN_VS         = -1_500.0   # ft/min (less negative = too slow)
    _CRIT_VS         = -800.0

    _ALERT_MAP = {
        "EMRG_DESCENT_WARNING": {
            "id": "EMRG_DESCENT", "severity": "warning",
            "msg": "EMERGENCY DESCENT SLOW",
            "detail": "Idle thrust at altitude — increase descent rate to >2500 ft/min",
        },
        "EMRG_DESCENT_CRITICAL": {
            "id": "EMRG_DESCENT", "severity": "critical",
            "msg": "EMERGENCY DESCENT NOT ESTABLISHED",
            "detail": "Near-level flight at idle — initiate emergency descent immediately",
        },
    }

    def __init__(self, ws=None, perf=None):
        self._ws = ws
        self._last_alert: str | None = None

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        phase    = state.get("phase", "")
        throttle = state.get("throttle_pct", 100.0)
        alt      = state.get("alt_captain", 0.0)

        if phase == "COMPLETE":
            await self._clear(); return
        if phase not in self._ACTIVE_PHASES or throttle >= self._IDLE_THRESH or alt < self._MIN_ALT_FT:
            if self._last_alert:
                await self._clear()
            return

        vs = (state.get("vs_captain", 0.0) + state.get("vs_fo", 0.0)) / 2.0

        if vs > self._CRIT_VS:
            alert = "EMRG_DESCENT_CRITICAL"
        elif vs > self._WARN_VS:
            alert = "EMRG_DESCENT_WARNING"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  VS={vs:.0f} ft/min  alt={alt:.0f} ft  throttle={throttle:.0f}%")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] EMRG_DESCENT CLEAR — adequate descent rate")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "EMRG_DESCENT"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "EMRG_DESCENT"})
