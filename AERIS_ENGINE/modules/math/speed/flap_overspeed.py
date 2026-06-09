from core.data_bus import DataBus


class FlapOverspeedModule:
    """SPD-5 — Flap Overspeed (VFE exceedance).

    Fires when flaps are not fully retracted (flap_deg > 2°) and IAS
    approaches or exceeds the published maximum flap extension speed (VFE).

    WARN  IAS > VFE − 10 kts  (approaching limit — retract or reduce speed)
    CRIT  IAS ≥ VFE            (structural damage risk — retract immediately)

    Suppressed during GROUND_ROLL and ROTATION (flaps correctly extended at
    slow speeds).  Also suppressed in LANDING when flaps are intentionally
    extended — the alert only fires when the airspeed is too high for the
    current flap setting.
    """

    _SUPPRESS_PHASES = frozenset({"GROUND_ROLL", "ROTATION", "COMPLETE"})
    _FLAP_THRESHOLD  = 2.0   # degrees — ignore trace flap movement noise

    _ALERT_MAP = {
        "FLAP_OVERSPEED_WARNING": {
            "id": "FLAP_OVERSPEED", "severity": "warning",
            "msg": "FLAP OVERSPEED",
            "detail": "IAS approaching VFE — retract flaps or reduce speed immediately",
        },
        "FLAP_OVERSPEED_CRITICAL": {
            "id": "FLAP_OVERSPEED", "severity": "critical",
            "msg": "FLAP OVERSPEED — RETRACT",
            "detail": "IAS exceeds VFE — structural damage risk, retract flaps, reduce speed",
        },
    }

    def __init__(self, ws=None, perf=None):
        self._ws  = ws
        self._vfe = perf.vfe_kts if perf else 0.0
        self._last_alert: str | None = None

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        phase    = state.get("phase", "")
        flap_deg = state.get("flap_deg", 0.0)

        if phase == "COMPLETE":
            await self._clear(); return
        if phase in self._SUPPRESS_PHASES or self._vfe <= 0 or flap_deg < self._FLAP_THRESHOLD:
            return

        ias = (state.get("ias_captain", 0.0) + state.get("ias_fo", 0.0)) / 2.0

        if ias >= self._vfe:
            alert = "FLAP_OVERSPEED_CRITICAL"
        elif ias > self._vfe - 10.0:
            alert = "FLAP_OVERSPEED_WARNING"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  IAS={ias:.1f}  VFE={self._vfe:.0f}  flap={flap_deg:.0f}°  phase={phase}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] FLAP_OVERSPEED CLEAR")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "FLAP_OVERSPEED"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "FLAP_OVERSPEED"})
