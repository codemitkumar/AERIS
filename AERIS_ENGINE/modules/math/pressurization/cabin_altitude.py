from core.data_bus import DataBus


class CabinAltitudeModule:
    """PRESS-1 — Cabin Altitude Monitor.

    At high cabin altitude, hypoxia onset becomes a risk.
    FAR/CS 25 requires warning at 10 000 ft cabin altitude.

    WARN  cabin_alt_ft > 9 000 ft
    CRIT  cabin_alt_ft > 10 000 ft (regulatory warning threshold — don oxygen masks)
    """

    _SUPPRESS_PHASES = frozenset({"GROUND_ROLL", "ROTATION", "LANDING", "COMPLETE"})
    _WARN_FT         = 9_000.0
    _CRIT_FT         = 10_000.0

    _ALERT_MAP = {
        "CABIN_ALT_WARNING": {
            "id": "CABIN_ALT", "severity": "warning",
            "msg": "CABIN ALTITUDE HIGH",
            "detail": "Cabin altitude above 9 000 ft — monitor pressurization system",
        },
        "CABIN_ALT_CRITICAL": {
            "id": "CABIN_ALT", "severity": "critical",
            "msg": "CABIN ALTITUDE — DON OXYGEN",
            "detail": "Cabin altitude ≥ 10 000 ft — don oxygen masks, declare emergency, descend",
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

        cabin_alt = state.get("cabin_alt_ft", 0.0)

        if cabin_alt > self._CRIT_FT:
            alert = "CABIN_ALT_CRITICAL"
        elif cabin_alt > self._WARN_FT:
            alert = "CABIN_ALT_WARNING"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  cabin_alt={cabin_alt:.0f} ft  phase={phase}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] CABIN_ALT CLEAR — pressurization normal")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "CABIN_ALT"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "CABIN_ALT"})
