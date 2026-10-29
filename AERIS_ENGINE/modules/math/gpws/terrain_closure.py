from core.alerting import UNCHANGED, AlertLatch
from core.data_bus import DataBus
from core.rate import TimeWindow


class TerrainClosureModule:
    """GPWS-2 — Terrain Closure Rate (GPWS Mode 2).

    Detects when the ground is rising faster than the aircraft can climb.
    Computed as: closure_rate = −d(radio_alt)/dt (positive = terrain rising).

    WARN  closure > 1 500 ft/min  (terrain rising quickly)
    CRIT  closure > 3 000 ft/min  (imminent terrain contact — pull up)
    """

    _SUPPRESS_PHASES = frozenset({"GROUND_ROLL", "ROTATION", "LANDING", "COMPLETE"})
    _MAX_RA_FT       = 2_500.0
    _WARN_RATE       = 1_500.0
    _CRIT_RATE       = 3_000.0
    # Recorded radio altitude steps by up to 10 ft between samples, so a
    # shorter window turns quantisation into thousands of ft/min of closure.
    _WINDOW_S        = 1.0

    _ALERT_MAP = {
        "TERRAIN_CLOSURE_WARN": {
            "id": "TERRAIN_CLOSURE", "severity": "warning",
            "msg": "TERRAIN AHEAD",
            "detail": "Terrain closing rapidly — verify navigation, consider climb",
        },
        "TERRAIN_CLOSURE_CRIT": {
            "id": "TERRAIN_CLOSURE", "severity": "critical",
            "msg": "PULL UP — TERRAIN",
            "detail": "Terrain closure critical — execute GPWS escape immediately",
        },
    }

    def __init__(self, ws=None, perf=None):
        self._ws  = ws
        self._buf = TimeWindow(self._WINDOW_S)
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

        ra = state.get("radio_alt_ft", state.get("alt_captain", 99999.0))
        self._buf.push(state, ra)

        if ra > self._MAX_RA_FT or not self._buf.full:
            if self._latch.current:
                await self._clear()
            return

        # Between the means of the two halves so quantisation cancels, over
        # real elapsed seconds - dividing by tick count understated this 30x.
        buf = list(self._buf)
        half = len(buf) // 2
        old = sum(buf[:half]) / half
        new = sum(buf[half:]) / len(buf[half:])
        closure = (old - new) / (self._buf.span_s / 2.0) * 60.0

        if closure > self._CRIT_RATE:
            alert = "TERRAIN_CLOSURE_CRIT"
        elif closure > self._WARN_RATE:
            alert = "TERRAIN_CLOSURE_WARN"
        else:
            alert = None

        alert = self._latch.update(state, alert)
        if alert is UNCHANGED:
            return

        if alert:
            print(f"[ALERT] {alert}  closure={closure:.0f} ft/min  RA={ra:.0f} ft  phase={phase}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] TERRAIN_CLOSURE CLEAR — terrain safe")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "TERRAIN_CLOSURE"})

    async def _clear(self) -> None:
        if self._latch.current is not None:
            self._latch.reset()
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "TERRAIN_CLOSURE"})
