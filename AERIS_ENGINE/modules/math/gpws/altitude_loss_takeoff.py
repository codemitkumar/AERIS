from core.data_bus import DataBus


class AltitudeLossTakeoffModule:
    """GPWS-3 — Altitude Loss After Takeoff (GPWS Mode 3).

    After lift-off the aircraft must not lose altitude while gear is still in
    transit or shortly after retraction.  Any significant altitude loss in
    the climb phase is a terrain risk.

    Tracks max altitude achieved since ROTATION; fires if current alt drops
    more than 50 ft (warn) or 100 ft (crit) below that peak.

    Active only during ROTATION and CLIMB phases below 1 500 ft RA.
    """

    _ACTIVE_PHASES = frozenset({"ROTATION", "CLIMB"})
    _MAX_RA_FT     = 1_500.0
    _WARN_DROP_FT  = 50.0
    _CRIT_DROP_FT  = 100.0

    _ALERT_MAP = {
        "ALT_LOSS_TKOF_WARN": {
            "id": "ALT_LOSS_TKOF", "severity": "warning",
            "msg": "ALTITUDE LOSS AFTER TAKEOFF",
            "detail": "Aircraft losing altitude after liftoff — check engine power",
        },
        "ALT_LOSS_TKOF_CRIT": {
            "id": "ALT_LOSS_TKOF", "severity": "critical",
            "msg": "DON'T SINK — TAKEOFF",
            "detail": "Significant altitude loss after takeoff — apply TOGA, execute escape manoeuvre",
        },
    }

    def __init__(self, ws=None, perf=None):
        self._ws      = ws
        self._peak_ft = 0.0
        self._active  = False
        self._last_alert: str | None = None

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        phase = state.get("phase", "")

        if phase == "COMPLETE":
            await self._clear(); return

        if phase == "GROUND_ROLL":
            self._peak_ft = 0.0
            self._active  = False
            return

        ra = state.get("radio_alt_ft", state.get("alt_captain", 99999.0))

        if phase in self._ACTIVE_PHASES and ra < self._MAX_RA_FT:
            self._active = True
            self._peak_ft = max(self._peak_ft, ra)
        elif phase not in self._ACTIVE_PHASES:
            self._active = False
            if self._last_alert:
                await self._clear()
            return

        if not self._active or self._peak_ft < 10.0:
            return

        drop = self._peak_ft - ra

        if drop > self._CRIT_DROP_FT:
            alert = "ALT_LOSS_TKOF_CRIT"
        elif drop > self._WARN_DROP_FT:
            alert = "ALT_LOSS_TKOF_WARN"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  RA={ra:.0f}  peak={self._peak_ft:.0f}  drop={drop:.0f} ft  phase={phase}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] ALT_LOSS_TKOF CLEAR — climbing normally")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "ALT_LOSS_TKOF"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "ALT_LOSS_TKOF"})
