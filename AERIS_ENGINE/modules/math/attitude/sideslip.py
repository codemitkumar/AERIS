from core.data_bus import DataBus


class SideslipModule:
    """ATT-5 — Sideslip / Skid / Slip Monitor.

    Excessive sideslip (ball out of center) indicates uncoordinated flight:
    either rudder mis-input or thrust/control asymmetry.  On approach it
    increases landing distance and causes directional control problems.

    Modelled via yaw_rate_deg_s as a proxy for sideslip when beta is not
    directly in the state dict.

    WARN  |yaw_rate| > 3 °/s  (noticeable sideslip)
    CRIT  |yaw_rate| > 8 °/s  (significant uncoordinated flight)
    """

    _SUPPRESS_PHASES = frozenset({"GROUND_ROLL", "ROTATION", "COMPLETE"})
    _WARN_YAW_RATE   = 3.0
    _CRIT_YAW_RATE   = 8.0

    _ALERT_MAP = {
        "SIDESLIP_WARNING": {
            "id": "SIDESLIP", "severity": "warning",
            "msg": "SIDESLIP DETECTED",
            "detail": "Uncoordinated flight — apply rudder to centre the ball",
        },
        "SIDESLIP_CRITICAL": {
            "id": "SIDESLIP", "severity": "critical",
            "msg": "SEVERE SIDESLIP",
            "detail": "Extreme sideslip — check for engine failure or rudder jam",
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

        # Prefer explicit sideslip_deg if available, else use yaw_rate proxy
        beta = abs(state.get("sideslip_deg", state.get("yaw_rate_deg_s", 0.0)))

        if beta > self._CRIT_YAW_RATE:
            alert = "SIDESLIP_CRITICAL"
        elif beta > self._WARN_YAW_RATE:
            alert = "SIDESLIP_WARNING"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  sideslip_proxy={beta:.1f}  phase={phase}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] SIDESLIP CLEAR — coordinated flight")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "SIDESLIP"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "SIDESLIP"})
