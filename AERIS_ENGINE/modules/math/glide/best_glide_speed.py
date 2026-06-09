from core.data_bus import DataBus


class BestGlideSpeedModule:
    """GLI-2 — Best Glide Speed Monitor.

    During an engine-out scenario (throttle < 5 % and airborne), monitors
    whether IAS is near the published best-glide speed.  Deviating from
    best-glide speed by more than 15 % reduces glide range significantly.

    WARN  IAS deviates > 15 % from best_glide_kts
    CRIT  IAS deviates > 30 % from best_glide_kts (major range loss)

    Only fires when: throttle_pct < 5 % AND aircraft is airborne (not GROUND_ROLL).
    This avoids false alerts during normal idle descent.
    """

    _SUPPRESS_PHASES   = frozenset({"GROUND_ROLL", "ROTATION", "LANDING", "COMPLETE"})
    _IDLE_THROTTLE_PCT = 5.0
    _WARN_DEV_PCT      = 0.15
    _CRIT_DEV_PCT      = 0.30

    _ALERT_MAP = {
        "BEST_GLIDE_WARNING": {
            "id": "BEST_GLIDE", "severity": "warning",
            "msg": "BEST GLIDE SPEED",
            "detail": "Engine-out — adjust to best-glide speed to maximise range",
        },
        "BEST_GLIDE_CRITICAL": {
            "id": "BEST_GLIDE", "severity": "critical",
            "msg": "GLIDE RANGE SEVERELY REDUCED",
            "detail": "Speed far from best glide — significant range loss, correct immediately",
        },
    }

    def __init__(self, ws=None, perf=None):
        self._ws             = ws
        self._best_glide_kts = perf.best_glide_kts if perf else 0.0
        self._last_alert: str | None = None

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        phase    = state.get("phase", "")
        throttle = state.get("throttle_pct", 100.0)

        if phase == "COMPLETE":
            await self._clear(); return
        if phase in self._SUPPRESS_PHASES or self._best_glide_kts <= 0:
            return
        if throttle >= self._IDLE_THROTTLE_PCT:
            if self._last_alert:
                await self._clear()
            return

        ias       = (state.get("ias_captain", 0.0) + state.get("ias_fo", 0.0)) / 2.0
        deviation = abs(ias - self._best_glide_kts) / self._best_glide_kts

        if deviation > self._CRIT_DEV_PCT:
            alert = "BEST_GLIDE_CRITICAL"
        elif deviation > self._WARN_DEV_PCT:
            alert = "BEST_GLIDE_WARNING"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  IAS={ias:.1f}  best_glide={self._best_glide_kts:.0f}  dev={deviation*100:.0f}%  phase={phase}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] BEST_GLIDE CLEAR — speed near best glide")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "BEST_GLIDE"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "BEST_GLIDE"})
