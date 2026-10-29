from core.alerting import UNCHANGED, AlertLatch
from core.data_bus import DataBus


class MinDiversionFuelModule:
    """FUEL-4 — Minimum Diversion Fuel Monitor.

    Checks whether remaining fuel is sufficient to divert to the nearest
    alternate airport plus fly the ICAO final reserve (30 min jet / 45 min piston).

    min_required_lbs = fuel_reserve_min_lbs  (set by the flight source; X-Plane
                        gives the 30 min final reserve only)

    WARN  fuel_total_lbs < min_required_lbs × 1.10  (10% margin)
    CRIT  fuel_total_lbs < min_required_lbs           (at minimum, divert now)
    """

    _SUPPRESS_PHASES = frozenset({"GROUND_ROLL", "ROTATION", "LANDING", "COMPLETE"})

    _ALERT_MAP = {
        "MIN_DIVERT_FUEL_WARN": {
            "id": "MIN_DIVERT_FUEL", "severity": "warning",
            "msg": "APPROACHING MINIMUM DIVERSION FUEL",
            "detail": "Fuel within 10% of minimum required for diversion — consider declaring emergency",
        },
        "MIN_DIVERT_FUEL_CRIT": {
            "id": "MIN_DIVERT_FUEL", "severity": "critical",
            "msg": "MINIMUM DIVERSION FUEL",
            "detail": "Fuel at minimum diversion level — declare MAYDAY, divert immediately",
        },
    }

    def __init__(self, ws=None, perf=None):
        self._ws = ws
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

        fuel_lbs    = state.get("fuel_total_lbs", 0.0)
        reserve_lbs = state.get("fuel_reserve_min_lbs", 0.0)

        if reserve_lbs <= 0:
            return

        if fuel_lbs < reserve_lbs:
            alert = "MIN_DIVERT_FUEL_CRIT"
        elif fuel_lbs < reserve_lbs * 1.10:
            alert = "MIN_DIVERT_FUEL_WARN"
        else:
            alert = None

        alert = self._latch.update(state, alert)
        if alert is UNCHANGED:
            return

        if alert:
            print(f"[ALERT] {alert}  fuel={fuel_lbs:.0f} lbs  min_required={reserve_lbs:.0f} lbs  margin={fuel_lbs-reserve_lbs:.0f} lbs")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] MIN_DIVERT_FUEL CLEAR — diversion fuel adequate")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "MIN_DIVERT_FUEL"})

    async def _clear(self) -> None:
        if self._latch.current is not None:
            self._latch.reset()
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "MIN_DIVERT_FUEL"})
