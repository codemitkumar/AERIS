import collections
from core.data_bus import DataBus


class RapidDecompressionModule:
    """PRESS-2 — Rapid Decompression Detector.

    A rapid rise in cabin altitude indicates a pressurisation breach.
    Normal climb of cabin alt is < 300 ft/min; fuselage breach can drive
    > 2 000 ft/min.

    WARN  cabin_alt climb rate > 500 ft/min
    CRIT  cabin_alt climb rate > 1 500 ft/min (explosive / rapid decompression)
    """

    _SUPPRESS_PHASES = frozenset({"GROUND_ROLL", "ROTATION", "LANDING", "COMPLETE"})
    _WINDOW          = 5          # ticks for rate calc (≈ 5 s at 1 Hz dispatch)
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
        self._buf   = collections.deque(maxlen=self._WINDOW)
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
        self._buf.append(cabin_alt)

        if len(self._buf) < self._WINDOW:
            return

        # Rate in ft/min: (newest − oldest) / window_ticks × 60
        rate = (self._buf[-1] - self._buf[0]) / self._WINDOW * 60.0

        if rate > self._CRIT_RATE:
            alert = "RAPID_DECOMP_CRITICAL"
        elif rate > self._WARN_RATE:
            alert = "RAPID_DECOMP_WARNING"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  cabin_alt={cabin_alt:.0f} ft  rate={rate:.0f} ft/min  phase={phase}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] RAPID_DECOMP CLEAR — cabin altitude stable")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "RAPID_DECOMP"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "RAPID_DECOMP"})
