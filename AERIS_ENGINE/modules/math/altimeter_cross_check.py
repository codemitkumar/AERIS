from core.data_bus import DataBus


class AltimeterCrossCheckModule:
    """PF/PM altimeter cross-check — alerts when captain and FO ADCs disagree beyond limits."""

    WARN_FT = 200   # ft — first caution; QNH mis-set or ADC degradation
    CRIT_FT = 500   # ft — severe disagree; do not rely on either indicator

    _ALERT_MAP = {
        "ALT_DISAGREE_WARN": {
            "id":       "ALT_DISAGREE",
            "severity": "warning",
            "msg":      "ALT DISAGREE",
            "detail":   "Captain/FO altimeters differ >200 ft — cross-check QNH and standby altimeter",
        },
        "ALT_DISAGREE_CRIT": {
            "id":       "ALT_DISAGREE",
            "severity": "critical",
            "msg":      "ALT DISAGREE",
            "detail":   "Captain/FO altimeters differ >500 ft — unreliable, use standby altimeter",
        },
    }

    def __init__(self, ws=None):
        self._ws = ws
        self._last_alert: str | None = None

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        alt_cap = state.get("alt_captain", 0.0)
        alt_fo  = state.get("alt_fo",      0.0)
        phase   = state.get("phase", "")

        # Both instruments read near field elevation on the ground — suppress there
        if phase in ("GROUND_ROLL", "ROTATION", "COMPLETE"):
            alert = None
        else:
            delta = abs(alt_cap - alt_fo)
            if delta >= self.CRIT_FT:
                alert = "ALT_DISAGREE_CRIT"
            elif delta >= self.WARN_FT:
                alert = "ALT_DISAGREE_WARN"
            else:
                alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            delta = abs(alt_cap - alt_fo)
            print(
                f"[ALERT] {alert}  "
                f"cap={alt_cap:.0f}ft  fo={alt_fo:.0f}ft  delta={delta:.0f}ft  phase={phase}"
            )
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] ALT CROSS-CHECK OK — captain/FO altimeters agree")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "ALT_DISAGREE"})
