from core.alerting import UNCHANGED, AlertLatch
from core.data_bus import DataBus
from core.rate import TimeWindow


class RapidDecompressionModule:
    """PRESS-2 — Rapid Decompression Detector.

    A rapid rise in cabin altitude indicates a pressurisation breach.
    Normal climb of cabin alt is < 300 ft/min; fuselage breach can drive
    > 2 000 ft/min.

    WARN  cabin_alt climb rate > 500 ft/min
    CRIT  cabin_alt climb rate > 1 500 ft/min (explosive / rapid decompression)
    """

    _SUPPRESS_PHASES = frozenset({"GROUND_ROLL", "ROTATION", "LANDING", "COMPLETE"})
    _WINDOW_S        = 1.5        # seconds of cabin-altitude history for the rate
    _WARN_RATE       = 500.0      # ft/min
    _CRIT_RATE       = 1_500.0

    _ALERT_MAP = {
        "RAPID_DECOMP_WARNING": {
            "id": "RAPID_DECOMP", "severity": "warning",
            "msg": "RAPID DECOMPRESSION",
            "detail": "Cabin altitude rising rapidly — check pressurization, begin descent",
        },
        "RAPID_DECOMP_CRITICAL": {
            "id": "RAPID_DECOMP", "severity": "critical",
            "msg": "EXPLOSIVE DECOMPRESSION",
            "detail": "Cabin altitude spiking — don masks, declare MAYDAY, emergency descent NOW",
        },
    }

    def __init__(self, ws=None, perf=None):
        self._ws    = ws
        self._buf   = TimeWindow(self._WINDOW_S)
        self._latch = AlertLatch(
            severity_of=lambda a: self._ALERT_MAP.get(a, {}).get("severity", "warning"))

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        phase = state.get("phase", "")
        if phase == "COMPLETE":
            await self._clear(); return
        if phase in self._SUPPRESS_PHASES:
            return

        cabin_alt = state.get("cabin_alt_ft", 0.0)
        self._buf.push(state, cabin_alt)

        if not self._buf.full:
            return

        # Over real elapsed seconds - dividing by tick count understated
        # this ~30x at 30 Hz.
        rate = (self._buf[-1] - self._buf[0]) / self._buf.span_s * 60.0

        if rate > self._CRIT_RATE:
            alert = "RAPID_DECOMP_CRITICAL"
        elif rate > self._WARN_RATE:
            alert = "RAPID_DECOMP_WARNING"
        else:
            alert = None

        alert = self._latch.update(state, alert)
        if alert is UNCHANGED:
            return

        if alert:
            print(f"[ALERT] {alert}  cabin_alt={cabin_alt:.0f} ft  rate={rate:.0f} ft/min  phase={phase}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] RAPID_DECOMP CLEAR — cabin altitude stable")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "RAPID_DECOMP"})

    async def _clear(self) -> None:
        if self._latch.current is not None:
            self._latch.reset()
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "RAPID_DECOMP"})
