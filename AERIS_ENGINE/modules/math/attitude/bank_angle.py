from core.data_bus import DataBus


class BankAngleModule:
    """ATT-2 — Excessive Bank Angle.

    Monitors bank angle with phase-aware thresholds.  During approach and
    landing, even moderate bank is dangerous due to ground proximity.

    WARN  |bank| > 30°  (cruise) / > 15° (approach/landing below 1000 ft)
    CRIT  |bank| > 45°  (cruise) / > 25° (approach/landing)
    """

    _SUPPRESS_PHASES = frozenset({"GROUND_ROLL", "COMPLETE"})
    _APPROACH_ALT_FT = 1_000.0

    _ALERT_MAP = {
        "BANK_ANGLE_WARNING": {
            "id": "BANK_ANGLE", "severity": "warning",
            "msg": "BANK ANGLE",
            "detail": "Excessive bank angle — reduce bank to recover lift and prevent over-stress",
        },
        "BANK_ANGLE_CRITICAL": {
            "id": "BANK_ANGLE", "severity": "critical",
            "msg": "BANK ANGLE — CRITICAL",
            "detail": "Extreme bank angle — immediate recovery required",
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

        bank = abs(state.get("bank_deg", 0.0))
        alt  = state.get("alt_captain", 9999.0)

        low_alt = (phase in ("DESCENT", "LANDING")) and alt < self._APPROACH_ALT_FT

        if low_alt:
            warn_limit, crit_limit = 15.0, 25.0
        else:
            warn_limit, crit_limit = 30.0, 45.0

        if bank > crit_limit:
            alert = "BANK_ANGLE_CRITICAL"
        elif bank > warn_limit:
            alert = "BANK_ANGLE_WARNING"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  bank={bank:.1f}°  low_alt={low_alt}  phase={phase}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] BANK_ANGLE CLEAR — within limits")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "BANK_ANGLE"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "BANK_ANGLE"})
