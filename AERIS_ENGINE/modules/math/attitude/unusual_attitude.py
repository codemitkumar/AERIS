from core.data_bus import DataBus


class UnusualAttitudeModule:
    """ATT-1 — Unusual Attitude Detection.

    Detects pitch + bank combinations that place the aircraft in an unusual
    attitude requiring immediate recovery.

    WARN  |pitch| > 20° OR |bank| > 35°  (high but recoverable)
    CRIT  |pitch| > 30° OR |bank| > 60°  (unusual attitude — immediate recovery)
    """

    _SUPPRESS_PHASES = frozenset({"GROUND_ROLL", "ROTATION", "COMPLETE"})

    _ALERT_MAP = {
        "UNUSUAL_ATT_WARNING": {
            "id": "UNUSUAL_ATT", "severity": "warning",
            "msg": "UNUSUAL ATTITUDE",
            "detail": "Excessive pitch or bank — verify attitude and return to normal flight",
        },
        "UNUSUAL_ATT_CRITICAL": {
            "id": "UNUSUAL_ATT", "severity": "critical",
            "msg": "UNUSUAL ATTITUDE — RECOVER",
            "detail": "Extreme pitch or bank — execute unusual attitude recovery immediately",
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

        pitch = state.get("pitch_deg", 0.0)
        bank  = state.get("bank_deg",  0.0)

        if abs(pitch) > 30.0 or abs(bank) > 60.0:
            alert = "UNUSUAL_ATT_CRITICAL"
        elif abs(pitch) > 20.0 or abs(bank) > 35.0:
            alert = "UNUSUAL_ATT_WARNING"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  pitch={pitch:.1f}°  bank={bank:.1f}°  phase={phase}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] UNUSUAL_ATT CLEAR — attitude normal")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "UNUSUAL_ATT"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "UNUSUAL_ATT"})
