import math
from core.data_bus import DataBus


class CrosswindLimitModule:
    """APPR-3 — Crosswind Limit Monitor.

    Computes crosswind component from wind speed, wind direction, and runway
    heading.  Exceeding demonstrated crosswind limits is a landing hazard.

    crosswind = wind_speed × |sin(wind_angle_off_nose)|

    WARN  crosswind > max_crosswind_kts × 0.85  (approaching limit)
    CRIT  crosswind > max_crosswind_kts          (at or beyond demonstrated limit)

    State fields used: wind_speed_kts, wind_dir_deg, hdg_deg.
    Limit comes from perf.max_crosswind_kts.

    Active only during DESCENT and LANDING phases.
    """

    _ACTIVE_PHASES = frozenset({"DESCENT", "LANDING"})

    _ALERT_MAP = {
        "CROSSWIND_LIMIT_WARN": {
            "id": "CROSSWIND", "severity": "warning",
            "msg": "CROSSWIND HIGH",
            "detail": "Approaching demonstrated crosswind limit — consider alternate runway",
        },
        "CROSSWIND_LIMIT_CRIT": {
            "id": "CROSSWIND", "severity": "critical",
            "msg": "CROSSWIND LIMIT EXCEEDED",
            "detail": "Beyond demonstrated crosswind — divert or use alternate runway",
        },
    }

    def __init__(self, ws=None, perf=None):
        self._ws          = ws
        self._max_xw_kts  = perf.max_crosswind_kts if perf else 25.0
        self._last_alert: str | None = None

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        phase = state.get("phase", "")
        if phase == "COMPLETE":
            await self._clear(); return
        if phase not in self._ACTIVE_PHASES:
            if self._last_alert:
                await self._clear()
            return

        wind_spd = state.get("wind_speed_kts", 0.0)
        wind_dir = state.get("wind_dir_deg",  0.0)
        hdg      = state.get("hdg_deg",        0.0)

        relative_angle = (wind_dir - hdg) % 360.0
        if relative_angle > 180.0:
            relative_angle -= 360.0
        crosswind = abs(wind_spd * math.sin(math.radians(relative_angle)))

        state["crosswind_kts"] = round(crosswind, 1)

        limit = self._max_xw_kts
        if crosswind > limit:
            alert = "CROSSWIND_LIMIT_CRIT"
        elif crosswind > limit * 0.85:
            alert = "CROSSWIND_LIMIT_WARN"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  crosswind={crosswind:.1f} kts  limit={limit:.0f} kts  phase={phase}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] CROSSWIND CLEAR — within limits")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "CROSSWIND"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "CROSSWIND"})
