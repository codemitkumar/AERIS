from core.data_bus import DataBus


class CruiseAltitudeModule:
    """PERF-1 — Cruise Altitude Optimality Monitor.

    Checks whether the aircraft is cruising at or near its optimal/filed altitude.
    Significant deviation from filed cruise altitude wastes fuel and may violate
    ATC clearance.

    WARN  |alt − cruise_alt_ft| > 500 ft for sustained cruise
    CRIT  |alt − cruise_alt_ft| > 1 500 ft

    Uses perf.cruise_alt_ft as filed altitude.
    Only active in CRUISE phase.
    """

    _SUPPRESS_PHASES = frozenset({"GROUND_ROLL", "ROTATION", "CLIMB", "DESCENT", "LANDING", "COMPLETE"})
    _WARN_DEV_FT     = 500.0
    _CRIT_DEV_FT     = 1_500.0

    _ALERT_MAP = {
        "CRUISE_ALT_DEV_WARN": {
            "id": "CRUISE_ALT_DEV", "severity": "warning",
            "msg": "OFF CRUISE ALTITUDE",
            "detail": "Altitude deviates > 500 ft from filed cruise level — check altimetry/ATC",
        },
        "CRUISE_ALT_DEV_CRIT": {
            "id": "CRUISE_ALT_DEV", "severity": "critical",
            "msg": "SIGNIFICANT ALTITUDE DEVIATION",
            "detail": "Altitude > 1500 ft from cruise level — TCAS / terrain risk, verify position",
        },
    }

    def __init__(self, ws=None, perf=None):
        self._ws         = ws
        self._cruise_alt = perf.cruise_alt_ft if perf else 35_000.0
        self._last_alert: str | None = None

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        phase = state.get("phase", "")
        if phase == "COMPLETE":
            await self._clear(); return
        if phase != "CRUISE":
            if self._last_alert:
                await self._clear()
            return

        alt = state.get("alt_captain", self._cruise_alt)
        dev = abs(alt - self._cruise_alt)

        if dev > self._CRIT_DEV_FT:
            alert = "CRUISE_ALT_DEV_CRIT"
        elif dev > self._WARN_DEV_FT:
            alert = "CRUISE_ALT_DEV_WARN"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  alt={alt:.0f}  cruise_alt={self._cruise_alt:.0f}  dev={dev:.0f} ft")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] CRUISE_ALT_DEV CLEAR — on altitude")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "CRUISE_ALT_DEV"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "CRUISE_ALT_DEV"})
