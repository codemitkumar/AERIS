import collections
from core.data_bus import DataBus


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
    _WINDOW          = 3

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
        self._buf = collections.deque(maxlen=self._WINDOW)
        self._last_alert: str | None = None

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        phase = state.get("phase", "")
        if phase == "COMPLETE":
            await self._clear(); return
        if phase in self._SUPPRESS_PHASES:
            return

        ra = state.get("radio_alt_ft", state.get("alt_captain", 99999.0))
        self._buf.append(ra)

        if ra > self._MAX_RA_FT or len(self._buf) < self._WINDOW:
            if self._last_alert:
                await self._clear()
            return

        # Closure rate = drop in radio altitude per minute
        closure = (self._buf[0] - self._buf[-1]) / self._WINDOW * 60.0

        if closure > self._CRIT_RATE:
            alert = "TERRAIN_CLOSURE_CRIT"
        elif closure > self._WARN_RATE:
            alert = "TERRAIN_CLOSURE_WARN"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  closure={closure:.0f} ft/min  RA={ra:.0f} ft  phase={phase}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] TERRAIN_CLOSURE CLEAR — terrain safe")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "TERRAIN_CLOSURE"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "TERRAIN_CLOSURE"})
