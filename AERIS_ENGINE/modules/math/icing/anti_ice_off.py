from core.data_bus import DataBus


class AntiIceOffModule:
    """ICE-2 — Anti-Ice System Off in Icing Conditions.

    Fires when icing conditions are detected (OAT in envelope) but the
    anti-ice system is OFF.  This is a configuration hazard.

    WARN  icing conditions AND anti_ice == False
    CRIT  icing conditions AND anti_ice == False AND alt > 10 000 ft
          (extended exposure at altitude where ice accumulates fastest)
    """

    _SUPPRESS_PHASES = frozenset({"GROUND_ROLL", "COMPLETE"})
    _HIGH_ALT_FT     = 10_000.0
    _OAT_HIGH_C      = 5.0
    _OAT_LOW_C       = -20.0

    _ALERT_MAP = {
        "ANTI_ICE_OFF_WARN": {
            "id": "ANTI_ICE_OFF", "severity": "warning",
            "msg": "ANTI-ICE OFF",
            "detail": "Icing conditions detected with anti-ice OFF — activate immediately",
        },
        "ANTI_ICE_OFF_CRIT": {
            "id": "ANTI_ICE_OFF", "severity": "critical",
            "msg": "ANTI-ICE OFF AT ALTITUDE",
            "detail": "Anti-ice OFF in icing conditions above 10 000 ft — severe ice accumulation risk",
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

        oat        = state.get("oat_c", -99.0)
        anti_ice   = state.get("anti_ice", True)
        alt        = state.get("alt_captain", 0.0)
        in_envelope = self._OAT_LOW_C <= oat <= self._OAT_HIGH_C

        if not in_envelope or anti_ice:
            if self._last_alert:
                await self._clear()
            return

        if alt > self._HIGH_ALT_FT:
            alert = "ANTI_ICE_OFF_CRIT"
        else:
            alert = "ANTI_ICE_OFF_WARN"

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  OAT={oat:.1f}°C  alt={alt:.0f} ft  phase={phase}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "ANTI_ICE_OFF"})
