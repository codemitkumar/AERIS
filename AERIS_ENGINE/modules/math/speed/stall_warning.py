from core.data_bus import DataBus


class StallWarningModule:
    """SPD-3 — Stall Warning / Approach to Stall.

    Monitors IAS margin above the live stall speed (vs_kias computed by ADRpy
    from current gross weight and atmospheric density).  Fires two tiers:

    WARN  IAS < vs_kias × 1.20  (20 % above stall — stick-shaker territory)
    CRIT  IAS < vs_kias × 1.05  (5 % above stall — imminent stall / stick-push)

    The 1.20 factor matches the FAR 25 stall-warning requirement: a warning
    must activate at least 5 kt or 5 % (whichever is greater) before the stall.
    The module uses 20 % to give enough lead time for recovery inputs.

    Suppressed during GROUND_ROLL, ROTATION, and COMPLETE; also suppressed when
    vs_kias is 0 (ADRpy not available) to avoid false alerts.
    """

    _SUPPRESS_PHASES = frozenset({"GROUND_ROLL", "ROTATION", "COMPLETE"})
    _WARN_FACTOR = 1.20
    _CRIT_FACTOR = 1.05

    _ALERT_MAP = {
        "STALL_WARNING": {
            "id": "STALL", "severity": "warning",
            "msg": "STALL WARNING",
            "detail": "IAS approaching stall — reduce pitch, increase thrust, check configuration",
        },
        "STALL_CRITICAL": {
            "id": "STALL", "severity": "critical",
            "msg": "STALL — PUSH NOSE",
            "detail": "Imminent stall — push nose down, full thrust, wings level",
        },
    }

    def __init__(self, ws=None, perf=None):
        self._ws = ws
        self._last_alert: str | None = None

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        phase   = state.get("phase", "")
        vs_kias = state.get("vs_kias", 0.0)

        if phase == "COMPLETE":
            await self._clear(); return
        if phase in self._SUPPRESS_PHASES or vs_kias <= 0:
            return

        ias = (state.get("ias_captain", 0.0) + state.get("ias_fo", 0.0)) / 2.0

        if ias < vs_kias * self._CRIT_FACTOR:
            alert = "STALL_CRITICAL"
        elif ias < vs_kias * self._WARN_FACTOR:
            alert = "STALL_WARNING"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            margin = ias - vs_kias
            print(f"[ALERT] {alert}  IAS={ias:.1f} kts  Vstall={vs_kias:.1f} kts  margin={margin:.1f} kts  phase={phase}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] STALL CLEAR — adequate speed margin restored")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "STALL"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "STALL"})
