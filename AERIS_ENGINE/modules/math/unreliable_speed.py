from core.data_bus import DataBus


class UnreliableSpeedModule:
    """Detects unreliable airspeed by comparing captain/FO IAS against groundspeed.

    Publishes alerts to connected WebSocket clients when anomaly state changes.
    """

    IAS_DELTA_THRESHOLD = 10.0   # kts — captain vs FO disagree
    GS_BIAS_THRESHOLD   = 20.0   # kts — one side biased far from groundspeed

    _ALERT_MAP = {
        "UNRELIABLE_IAS_CAPTAIN": {
            "id":       "CAPT_ADC",
            "severity": "warning",
            "msg":      "CAPT ADC SUSPECT",
            "detail":   "Captain IAS unreliable — cross-check F/O instruments",
        },
        "UNRELIABLE_IAS_FO": {
            "id":       "FO_ADC",
            "severity": "warning",
            "msg":      "F/O ADC SUSPECT",
            "detail":   "F/O IAS unreliable — cross-check CAPT instruments",
        },
        "UNRELIABLE_IAS_BOTH": {
            "id":       "UNRELIABLE_SPD",
            "severity": "critical",
            "msg":      "UNRELIABLE AIRSPEED",
            "detail":   "Both ADC channels suspect — cross-check all instruments",
        },
    }

    def __init__(self, ws=None):
        self._ws = ws
        self._last_alert: str | None = None

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        ias_cap = state.get("ias_captain", 0.0)
        ias_fo  = state.get("ias_fo",      0.0)
        gs      = state.get("groundspeed_kts", 0.0)
        phase   = state.get("phase", "")
        v1      = state.get("v1_kts", 0.0)
        vr      = state.get("vr_kts", 0.0)

        delta    = abs(ias_cap - ias_fo)
        cap_bias = abs(ias_cap - gs)
        fo_bias  = abs(ias_fo  - gs)

        if delta < self.IAS_DELTA_THRESHOLD:
            alert = None
        elif cap_bias > fo_bias + self.GS_BIAS_THRESHOLD:
            alert = "UNRELIABLE_IAS_CAPTAIN"
        elif fo_bias > cap_bias + self.GS_BIAS_THRESHOLD:
            alert = "UNRELIABLE_IAS_FO"
        else:
            alert = "UNRELIABLE_IAS_BOTH"

        if alert == self._last_alert:
            return

        self._last_alert = alert

        if alert:
            speed_ctx = self._speed_context(gs, v1, vr, phase)
            print(
                f"[ALERT] {alert}  "
                f"cap={ias_cap:.1f} fo={ias_fo:.1f} gs={gs:.1f} kts  "
                f"phase={phase}  {speed_ctx}"
            )
            if self._ws:
                payload = {**self._ALERT_MAP[alert], "topic": "alert"}
                await self._ws.broadcast_alert(payload)
        else:
            print("[ALERT] IAS NORMAL — airspeed cross-check OK")
            # Clear all airspeed-related alerts
            for alert_def in self._ALERT_MAP.values():
                if self._ws:
                    await self._ws.broadcast_alert({"topic": "alert_clear", "id": alert_def["id"]})

    @staticmethod
    def _speed_context(gs: float, v1: float, vr: float, phase: str) -> str:
        if phase != "GROUND_ROLL" or v1 == 0:
            return f"(airborne phase)"
        if gs < v1:
            return f"BEFORE V1 (gs={gs:.1f} V1={v1:.1f}) — pre-decision"
        if gs < vr:
            return f"BETWEEN V1-VR (gs={gs:.1f} V1={v1:.1f} VR={vr:.1f}) — post-decision"
        return f"AFTER VR (gs={gs:.1f} VR={vr:.1f}) — rotation zone"
