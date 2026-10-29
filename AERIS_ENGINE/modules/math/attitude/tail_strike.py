from core.alerting import UNCHANGED, AlertLatch
from core.data_bus import DataBus


class TailStrikeModule:
    """ATT-6 — Tail Strike Monitor.

    Pitch against the aircraft's tail-contact attitude while the main gear
    is on, or within a few feet of, the runway — takeoff over-rotation,
    a flared-too-high landing, or de-rotating late after touchdown.

    WARN  pitch ≥ contact − 2°   tail clearance nearly gone
    CRIT  pitch ≥ contact − 1°   strike probable (the 1° covers 1 Hz pitch
                                 sampling and strut compression)

    A probable strike stays latched for the rest of the flight: the damage
    doesn't go away when the nose comes down, and crews routinely don't feel
    it (DCA22LA178 flew a go-around and a second landing without knowing).
    The checklist response is to not pressurise and land at the nearest
    suitable airport, which GRACE picks up from the active TAIL_STRIKE id.

    Inactive when the aircraft profile has no tailstrike_pitch_deg.
    """

    _NEAR_GROUND_RA_FT = 5.0     # antenna height: reads ~-3 ft with mains on the runway
    _MIN_SPEED_KTS     = 60.0    # below this the airplane can't rotate
    _WARN_MARGIN_DEG   = 2.0
    _CRIT_MARGIN_DEG   = 1.0

    _ALERT_MAP = {
        "TAIL_STRIKE_RISK": {
            "id": "TAIL_STRIKE", "severity": "warning",
            "msg": "PITCH — TAIL CLEARANCE",
            "detail": "Pitch near tail-contact attitude on the runway — lower the nose",
        },
        "TAIL_STRIKE_PROBABLE": {
            "id": "TAIL_STRIKE", "severity": "critical",
            "msg": "TAIL STRIKE PROBABLE",
            "detail": "Tail-contact attitude reached on the runway — do not pressurise, "
                      "land at nearest suitable airport",
        },
    }

    def __init__(self, ws=None, perf=None):
        self._ws = ws
        self._contact = getattr(perf, "tailstrike_pitch_deg", 0.0) or 0.0
        self._struck = False
        self._latch = AlertLatch(
            severity_of=lambda a: self._ALERT_MAP.get(a, {}).get("severity", "warning"))

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        if self._contact <= 0:
            return
        phase = state.get("phase", "")
        if phase == "COMPLETE":
            await self._clear(); return
        if self._struck:
            return

        pitch = state.get("pitch_deg", 0.0)
        speed = max(state.get("groundspeed_kts", 0.0), state.get("ias_captain", 0.0))
        ra = state.get("radio_alt_ft")
        near_runway = bool(state.get("weight_on_wheels")) or (
            ra is not None and ra < self._NEAR_GROUND_RA_FT)

        if not near_runway or speed < self._MIN_SPEED_KTS:
            alert = None
        elif pitch >= self._contact - self._CRIT_MARGIN_DEG:
            alert = "TAIL_STRIKE_PROBABLE"
        elif pitch >= self._contact - self._WARN_MARGIN_DEG:
            alert = "TAIL_STRIKE_RISK"
        else:
            alert = None

        alert = self._latch.update(state, alert)
        if alert is UNCHANGED:
            return

        if alert:
            if alert == "TAIL_STRIKE_PROBABLE":
                self._struck = True
            print(f"[ALERT] {alert}  pitch={pitch:.1f}°  limit={self._contact:.1f}°  "
                  f"RA={ra}  phase={phase}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] TAIL_STRIKE CLEAR — tail clearance restored")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "TAIL_STRIKE"})

    async def _clear(self) -> None:
        if self._latch.current is not None:
            self._latch.reset()
            self._struck = False
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "TAIL_STRIKE"})
