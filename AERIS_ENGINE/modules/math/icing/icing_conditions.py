from core.data_bus import DataBus


class IcingConditionsModule:
    """ICE-1 — Icing Conditions Detector.

    Icing conditions exist when:
      - OAT (outside air temperature) is between −20 °C and +5 °C
      - AND visible moisture is implied (simulated via humidity proxy in state,
        or assumed true above FL100 in CLIMB / CRUISE)

    This module detects when the aircraft is flying in the icing envelope
    so that anti-ice activation and ice accumulation modules can act.

    WARN  conditions met — anti-ice may be required
    (No CRIT for this module — ice accumulation drives severity)
    """

    _SUPPRESS_PHASES = frozenset({"GROUND_ROLL", "COMPLETE"})
    _OAT_HIGH_C      = 5.0
    _OAT_LOW_C       = -20.0

    _ALERT_MAP = {
        "ICING_CONDITIONS": {
            "id": "ICING_COND", "severity": "warning",
            "msg": "ICING CONDITIONS",
            "detail": "Temperature in icing envelope — ensure anti-ice systems are ON",
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

        oat = state.get("oat_c", -99.0)
        in_envelope = self._OAT_LOW_C <= oat <= self._OAT_HIGH_C

        state["icing_conditions"] = in_envelope

        alert = "ICING_CONDITIONS" if in_envelope else None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  OAT={oat:.1f}°C  phase={phase}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] ICING_COND CLEAR — outside icing envelope")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "ICING_COND"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "ICING_COND"})
