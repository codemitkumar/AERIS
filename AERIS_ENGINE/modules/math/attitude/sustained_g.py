import collections
from core.data_bus import DataBus


class SustainedGModule:
    """ATT-4 — Sustained G-Force Monitor.

    Monitors load factor (G) and sustained high-G manoeuvres.
    Structural limits vary by aircraft but typical transport limits are
    +2.5 G (flaps up) / +2.0 G (flaps down), −1.0 G.

    WARN  G > 2.0 or G < −0.5
    CRIT  G > 2.5 or G < −1.0  (envelope exceedance)

    Also fires if moderate G (> 1.5) is sustained > 10 ticks (structural fatigue).
    """

    _SUPPRESS_PHASES  = frozenset({"GROUND_ROLL", "COMPLETE"})
    _WARN_G_POS       = 2.0
    _CRIT_G_POS       = 2.5
    _WARN_G_NEG       = -0.5
    _CRIT_G_NEG       = -1.0
    _SUSTAIN_G        = 1.5
    _SUSTAIN_TICKS    = 10

    _ALERT_MAP = {
        "G_LOAD_WARNING": {
            "id": "G_LOAD", "severity": "warning",
            "msg": "HIGH G LOAD",
            "detail": "Load factor approaching structural limit — reduce manoeuvre aggressiveness",
        },
        "G_LOAD_CRITICAL": {
            "id": "G_LOAD", "severity": "critical",
            "msg": "G LIMIT EXCEEDED",
            "detail": "Load factor above structural limit — inspect aircraft after landing",
        },
        "G_NEGATIVE_WARNING": {
            "id": "G_LOAD", "severity": "warning",
            "msg": "NEGATIVE G",
            "detail": "Significant negative G — fuel/oil interruption possible",
        },
        "G_SUSTAINED_WARNING": {
            "id": "G_LOAD", "severity": "warning",
            "msg": "SUSTAINED G",
            "detail": "Moderate G sustained > 10 ticks — structural fatigue accumulation",
        },
    }

    def __init__(self, ws=None, perf=None):
        self._ws           = ws
        self._sustain_buf  = collections.deque(maxlen=self._SUSTAIN_TICKS)
        self._last_alert: str | None = None

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        phase = state.get("phase", "")
        if phase == "COMPLETE":
            await self._clear(); return
        if phase in self._SUPPRESS_PHASES:
            return

        g = state.get("g_load", 1.0)
        self._sustain_buf.append(g)

        if g > self._CRIT_G_POS:
            alert = "G_LOAD_CRITICAL"
        elif g < self._CRIT_G_NEG:
            alert = "G_NEGATIVE_WARNING"
        elif g > self._WARN_G_POS:
            alert = "G_LOAD_WARNING"
        elif g < self._WARN_G_NEG:
            alert = "G_NEGATIVE_WARNING"
        elif (len(self._sustain_buf) == self._SUSTAIN_TICKS
              and all(v > self._SUSTAIN_G for v in self._sustain_buf)):
            alert = "G_SUSTAINED_WARNING"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  g={g:.2f}G  phase={phase}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] G_LOAD CLEAR — load factor normal")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "G_LOAD"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "G_LOAD"})
