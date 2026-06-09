from core.data_bus import DataBus


class GearOverspeedModule:
    """SPD-6 — Gear Overspeed (VLO / VLE exceedance).

    Monitors IAS against published gear speed limits when gear is not fully
    retracted.  Not applicable to fixed-gear aircraft (vle_kts = 0).

    Two limits:
      VLO  Max speed during gear transit (TRANSIT state) — lower limit
      VLE  Max speed with gear extended/locked (DOWN state)

    WARN  IAS > limit − 10 kts
    CRIT  IAS ≥ limit
    """

    _ALERT_MAP = {
        "GEAR_OVERSPEED_WARNING": {
            "id": "GEAR_OVERSPEED", "severity": "warning",
            "msg": "GEAR OVERSPEED",
            "detail": "Approaching gear speed limit — reduce speed or retract gear",
        },
        "GEAR_OVERSPEED_CRITICAL": {
            "id": "GEAR_OVERSPEED", "severity": "critical",
            "msg": "GEAR OVERSPEED — REDUCE SPEED",
            "detail": "Gear speed limit exceeded — structural damage risk, reduce speed immediately",
        },
    }

    def __init__(self, ws=None, perf=None):
        self._ws  = ws
        self._vle = perf.vle_kts if perf else 0.0
        self._vlo = perf.vlo_kts if perf else 0.0
        self._last_alert: str | None = None

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        phase = state.get("phase", "")
        gear  = state.get("gear", "UP")

        if phase == "COMPLETE":
            await self._clear(); return
        if self._vle <= 0 or gear == "UP":
            return

        ias   = (state.get("ias_captain", 0.0) + state.get("ias_fo", 0.0)) / 2.0
        limit = self._vlo if gear == "TRANSIT" else self._vle

        if ias >= limit:
            alert = "GEAR_OVERSPEED_CRITICAL"
        elif ias > limit - 10.0:
            alert = "GEAR_OVERSPEED_WARNING"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  IAS={ias:.1f}  limit={limit:.0f}  gear={gear}  phase={phase}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] GEAR_OVERSPEED CLEAR")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "GEAR_OVERSPEED"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "GEAR_OVERSPEED"})
