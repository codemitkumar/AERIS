from core.alerting import UNCHANGED, AlertLatch
from core.data_bus import DataBus
from core.rate import TimeWindow, window_len


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

    # Real vertical speed carries ~1.3 ft/min of noise, so half a second
    # averages cleaner than the 2 s this needed against synthetic data.
    _VS_WINDOW_S = 0.5

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
        self._latch = AlertLatch(
            severity_of=lambda a: self._ALERT_MAP.get(a, {}).get("severity", "warning"))
        self._vs_buf = TimeWindow(self._VS_WINDOW_S)

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
            if self._latch.current is not None:
                self._latch.reset()
                if self._ws:
                    await self._ws.broadcast_alert({"topic": "alert_clear", "id": "UNCOMMANDED_DES"})
            return

        # Average both channels to halve the sensor noise before smoothing
        self._vs_buf.push(state, (vs_cap + vs_fo) / 2.0)

        # Require at least half the window to be populated before making a call
        if len(self._vs_buf) < window_len(state, self._VS_WINDOW_S) // 2:
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

        alert = self._latch.update(state, alert)
        if alert is UNCHANGED:
            return

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
