from core.data_bus import DataBus


class ManeuveringSpeedModule:
    """SPD-7 — Maneuvering Speed (Va) structural limit.

    Above Va, the aircraft structure may be damaged by full control deflections
    or sharp gusts.  This module fires when IAS exceeds Va while significant
    G-loading is present (indicating active maneuvering or turbulence).

    WARN  IAS > Va  AND  load_factor_g > 1.30
    CRIT  IAS > Va  AND  load_factor_g > 1.80

    Suppressed during GROUND_ROLL, ROTATION, LANDING, COMPLETE.
    Only meaningful in CLIMB and CRUISE where Va exceedance with G is plausible.
    """

    _SUPPRESS_PHASES = frozenset({"GROUND_ROLL", "ROTATION", "LANDING", "COMPLETE"})
    _G_WARN = 1.30
    _G_CRIT = 1.80

    _ALERT_MAP = {
        "MANEUVERING_SPEED_WARNING": {
            "id": "VA_OVERSPEED", "severity": "warning",
            "msg": "ABOVE Va — MANEUVER GENTLY",
            "detail": "IAS above maneuvering speed with G-loading — avoid abrupt control inputs",
        },
        "MANEUVERING_SPEED_CRITICAL": {
            "id": "VA_OVERSPEED", "severity": "critical",
            "msg": "STRUCTURAL OVERLOAD RISK",
            "detail": "High G above Va — risk of structural damage, reduce speed or G immediately",
        },
    }

    def __init__(self, ws=None, perf=None):
        self._ws = ws
        self._va = perf.va_kts if perf else 0.0
        self._last_alert: str | None = None

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        phase = state.get("phase", "")

        if phase == "COMPLETE":
            await self._clear(); return
        if phase in self._SUPPRESS_PHASES or self._va <= 0:
            return

        ias = (state.get("ias_captain", 0.0) + state.get("ias_fo", 0.0)) / 2.0
        g   = state.get("load_factor_g", 1.0)

        if ias > self._va and g > self._G_CRIT:
            alert = "MANEUVERING_SPEED_CRITICAL"
        elif ias > self._va and g > self._G_WARN:
            alert = "MANEUVERING_SPEED_WARNING"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  IAS={ias:.1f}  Va={self._va:.0f}  G={g:.2f}  phase={phase}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] VA_OVERSPEED CLEAR")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "VA_OVERSPEED"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "VA_OVERSPEED"})
