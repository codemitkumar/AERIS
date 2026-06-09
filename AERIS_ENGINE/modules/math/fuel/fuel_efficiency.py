import collections
from core.data_bus import DataBus


class FuelEfficiencyModule:
    """FUEL-5 — Fuel Efficiency Monitor (specific range).

    Specific range (SR) = ground_speed_kts / fuel_flow_total_lbs_hr  [nm/lb]
    Compares current SR to a baseline established early in cruise.

    A drop in SR means more fuel is being burned for the same progress —
    caused by unexpected headwind, altitude too low, engine degradation, etc.

    WARN  SR < baseline × 0.90  (10% efficiency loss)
    CRIT  SR < baseline × 0.80  (20% loss — significant problem)

    Active only in CRUISE phase; establishes baseline over first 60 ticks.
    """

    _SUPPRESS_PHASES  = frozenset({"GROUND_ROLL", "ROTATION", "CLIMB", "DESCENT", "LANDING", "COMPLETE"})
    _BASELINE_TICKS   = 60
    _WARN_RATIO       = 0.90
    _CRIT_RATIO       = 0.80

    _ALERT_MAP = {
        "FUEL_EFF_WARN": {
            "id": "FUEL_EFF", "severity": "warning",
            "msg": "FUEL EFFICIENCY DEGRADED",
            "detail": "Specific range 10% below cruise baseline — check altitude, speed, engine health",
        },
        "FUEL_EFF_CRIT": {
            "id": "FUEL_EFF", "severity": "critical",
            "msg": "FUEL EFFICIENCY SEVERELY DEGRADED",
            "detail": "Specific range 20% below baseline — significant fuel overburn, consider diversion",
        },
    }

    def __init__(self, ws=None, perf=None):
        self._ws       = ws
        self._baseline = None
        self._buf      = collections.deque(maxlen=self._BASELINE_TICKS)
        self._last_alert: str | None = None

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        phase = state.get("phase", "")
        if phase == "COMPLETE":
            await self._clear(); return

        if phase not in ("CRUISE",):
            if phase in self._SUPPRESS_PHASES and phase != "CRUISE":
                if self._last_alert:
                    await self._clear()
                return

        flow  = state.get("fuel_flow_total_lbs_hr", 0.0)
        gs    = state.get("gs_kts", state.get("ias_captain", 0.0))

        if flow <= 0 or gs <= 0:
            return

        sr = gs / flow

        if self._baseline is None:
            self._buf.append(sr)
            if len(self._buf) == self._BASELINE_TICKS:
                self._baseline = sum(self._buf) / len(self._buf)
            return

        state["specific_range_nm_lb"] = round(sr, 4)

        ratio = sr / self._baseline

        if ratio < self._CRIT_RATIO:
            alert = "FUEL_EFF_CRIT"
        elif ratio < self._WARN_RATIO:
            alert = "FUEL_EFF_WARN"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  SR={sr:.4f}  baseline={self._baseline:.4f}  ratio={ratio:.2f}  phase={phase}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] FUEL_EFF CLEAR — efficiency normal")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "FUEL_EFF"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "FUEL_EFF"})
