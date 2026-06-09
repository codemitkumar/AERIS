from core.data_bus import DataBus


class UnstableApproachModule:
    """APPR-1 — Unstable Approach Detector.

    An approach is stable when, below 1 000 ft AAL, the aircraft is:
      • On glidepath (dots within ±1.0)
      • At target speed (within +15/−5 of VREF)
      • Correct configuration (gear down, flap ≥ 25°)
      • Descent rate < 1 000 ft/min

    Any violation is an unstable approach.  WARN at first breach;
    CRIT if three or more criteria simultaneously violated.
    """

    _ACTIVE_PHASE = "DESCENT"
    _GATE_ALT_FT  = 1_000.0
    _VREF_FAST    = 15.0
    _VREF_SLOW    = 5.0
    _MAX_VS       = -1_000.0
    _MAX_GS_DOTS  = 1.0
    _LANDING_FLAP = 25.0

    _ALERT_MAP = {
        "UNSTABLE_APPROACH": {
            "id": "UNSTABLE_APPR", "severity": "warning",
            "msg": "UNSTABLE APPROACH",
            "detail": "Approach criteria violated below 1000 ft — stabilise or go around",
        },
        "UNSTABLE_APPROACH_CRIT": {
            "id": "UNSTABLE_APPR", "severity": "critical",
            "msg": "GO AROUND — UNSTABLE",
            "detail": "Multiple approach criteria violated — execute go-around immediately",
        },
    }

    def __init__(self, ws=None, perf=None):
        self._ws   = ws
        self._last_alert: str | None = None

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        phase = state.get("phase", "")
        alt   = state.get("alt_captain", 9999.0)

        if phase == "COMPLETE":
            await self._clear(); return
        if phase != self._ACTIVE_PHASE or alt > self._GATE_ALT_FT:
            if self._last_alert:
                await self._clear()
            return

        vref = state.get("vref_kts", 0.0)
        ias  = (state.get("ias_captain", 0.0) + state.get("ias_fo", 0.0)) / 2.0
        vs   = (state.get("vs_captain", 0.0) + state.get("vs_fo", 0.0)) / 2.0
        dots = state.get("glideslope_dots", 0.0)
        gear = state.get("gear", "UP")
        flap = state.get("flap_deg", 0.0)

        violations = 0
        if vref > 0:
            if ias > vref + self._VREF_FAST or ias < vref - self._VREF_SLOW:
                violations += 1
        if vs < self._MAX_VS:
            violations += 1
        if abs(dots) > self._MAX_GS_DOTS:
            violations += 1
        if gear != "DOWN" or flap < self._LANDING_FLAP:
            violations += 1

        if violations >= 3:
            alert = "UNSTABLE_APPROACH_CRIT"
        elif violations >= 1:
            alert = "UNSTABLE_APPROACH"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  violations={violations}  IAS={ias:.0f}  VS={vs:.0f}  dots={dots:.2f}  phase={phase}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] UNSTABLE_APPR CLEAR — approach stable")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "UNSTABLE_APPR"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "UNSTABLE_APPR"})
