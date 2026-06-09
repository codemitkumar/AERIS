from core.data_bus import DataBus


class PitchLimitModule:
    """ATT-3 — Pitch Limit Monitor.

    Detects excessive nose-up or nose-down pitch attitude.  High nose-up can
    lead to stall; high nose-down creates overspeed risk.

    WARN  pitch > +20° (nose-up) or < −10° (nose-down)
    CRIT  pitch > +30°           or < −20°

    Thresholds loosen during takeoff/climb (up to +25° allowed).
    """

    _SUPPRESS_PHASES = frozenset({"GROUND_ROLL", "COMPLETE"})

    _ALERT_MAP = {
        "PITCH_HIGH_WARNING": {
            "id": "PITCH_LIMIT", "severity": "warning",
            "msg": "PITCH HIGH",
            "detail": "Excessive nose-up pitch — risk of aerodynamic stall",
        },
        "PITCH_HIGH_CRITICAL": {
            "id": "PITCH_LIMIT", "severity": "critical",
            "msg": "PITCH LIMIT EXCEEDANCE",
            "detail": "Extreme nose-up pitch — push forward, add thrust",
        },
        "PITCH_LOW_WARNING": {
            "id": "PITCH_LIMIT", "severity": "warning",
            "msg": "PITCH LOW",
            "detail": "Excessive nose-down pitch — risk of overspeed",
        },
        "PITCH_LOW_CRITICAL": {
            "id": "PITCH_LIMIT", "severity": "critical",
            "msg": "PITCH LIMIT EXCEEDANCE",
            "detail": "Extreme nose-down pitch — pull up, reduce thrust",
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
        climb_phase = phase in ("ROTATION", "CLIMB")

        up_warn = 25.0 if climb_phase else 20.0
        up_crit = 35.0 if climb_phase else 30.0
        dn_warn, dn_crit = -10.0, -20.0

        if pitch > up_crit:
            alert = "PITCH_HIGH_CRITICAL"
        elif pitch > up_warn:
            alert = "PITCH_HIGH_WARNING"
        elif pitch < dn_crit:
            alert = "PITCH_LOW_CRITICAL"
        elif pitch < dn_warn:
            alert = "PITCH_LOW_WARNING"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  pitch={pitch:.1f}°  phase={phase}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] PITCH_LIMIT CLEAR — pitch normal")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "PITCH_LIMIT"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "PITCH_LIMIT"})
