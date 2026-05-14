from collections import deque
from core.data_bus import DataBus


class UncommandedDescentModule:
    """Detects uncommanded descent: significant altitude loss while climb or cruise is expected.

    Conditions that must ALL be true before alerting:
      - Phase is CLIMB or CRUISE (descent is not the commanded flight profile)
      - Smoothed VS is significantly negative
      - AP mode is not explicitly commanding descent
      - Throttle has not been pulled to idle (which would indicate intentional descent)

    Potential root causes: icing, thrust decay, AP trim runaway, pilot incapacitation,
    wind shear, or flight control fault.
    """

    # Rolling VS smoothing window (30 Hz × 2 s = 60 ticks)
    # Averages out the ±50 ft/min ADC noise before comparing against thresholds
    _VS_WINDOW = 60

    # Smoothed VS thresholds (ft/min, negative = descending)
    WARN_VS_FPM = -300   # modest but unexplained loss
    CRIT_VS_FPM = -800   # clear energy loss in a climb/cruise phase

    # Throttle at or below this % is treated as intentional idle/descent power
    IDLE_THROTTLE_PCT = 25.0

    # AP modes that explicitly command a descent — not "uncommanded" in those modes
    _DESCENT_MODES = frozenset({"DES", "APP", "FLARE", "ROLLOUT"})

    _ALERT_MAP = {
        "UNCOMMANDED_DESCENT_WARN": {
            "id":       "UNCOMMANDED_DES",
            "severity": "warning",
            "msg":      "UNCOMMAND DESCENT",
            "detail":   "Aircraft losing altitude with no descent commanded — check AP mode, throttle, and trim",
        },
        "UNCOMMANDED_DESCENT_CRIT": {
            "id":       "UNCOMMANDED_DES",
            "severity": "critical",
            "msg":      "UNCOMMAND DESCENT",
            "detail":   "Severe uncommanded altitude loss — check for icing, thrust decay, AP trim runaway, pilot incapacitation",
        },
    }

    def __init__(self, ws=None):
        self._ws = ws
        self._last_alert: str | None = None
        self._vs_buf: deque[float] = deque(maxlen=self._VS_WINDOW)

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        phase    = state.get("phase", "")
        ap_mode  = state.get("ap_mode", "")
        throttle = state.get("throttle_pct", 100.0)
        vs_cap   = state.get("vs_captain", 0.0)
        vs_fo    = state.get("vs_fo",      0.0)

        if phase not in ("CLIMB", "CRUISE"):
            # Clear history so stale readings don't poison the next climb/cruise entry
            self._vs_buf.clear()
            if self._last_alert is not None:
                self._last_alert = None
                if self._ws:
                    await self._ws.broadcast_alert({"topic": "alert_clear", "id": "UNCOMMANDED_DES"})
            return

        # Average both channels to halve the ±50 ft/min noise before smoothing
        self._vs_buf.append((vs_cap + vs_fo) / 2.0)

        # Require at least half the window to be populated before making a call
        if len(self._vs_buf) < self._VS_WINDOW // 2:
            return

        avg_vs = sum(self._vs_buf) / len(self._vs_buf)

        # Skip if AP has commanded descent or throttle is at idle
        if ap_mode in self._DESCENT_MODES or throttle <= self.IDLE_THROTTLE_PCT:
            alert = None
        elif avg_vs <= self.CRIT_VS_FPM:
            alert = "UNCOMMANDED_DESCENT_CRIT"
        elif avg_vs <= self.WARN_VS_FPM:
            alert = "UNCOMMANDED_DESCENT_WARN"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(
                f"[ALERT] {alert}  avg_vs={avg_vs:.0f} ft/min  "
                f"throttle={throttle:.1f}%  ap_mode={ap_mode}  phase={phase}"
            )
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] UNCOMMANDED_DESCENT CLEAR — VS normal for phase")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "UNCOMMANDED_DES"})
