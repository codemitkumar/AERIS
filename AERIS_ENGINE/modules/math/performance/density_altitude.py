from core.data_bus import DataBus


class DensityAltitudeModule:
    """PERF-2 — Density Altitude Calculator.

    Density altitude affects engine power output and lift generation.
    High density altitude means reduced performance — critical at hot, high airports.

    density_alt_ft = pressure_alt_ft + 120 × (OAT_c − ISA_temp_c)
    ISA_temp_c = 15 − (pressure_alt_ft / 1000) × 1.98

    WARN  density_alt_ft > pressure_alt_ft + 2 000 ft  (hot day, significant penalty)
    CRIT  density_alt_ft > pressure_alt_ft + 4 000 ft  (extreme density altitude)

    Publishes density_alt_ft into state for use by takeoff performance and AI modules.
    Active in GROUND_ROLL and ROTATION only (takeoff context).
    """

    _ACTIVE_PHASES = frozenset({"GROUND_ROLL", "ROTATION"})
    _WARN_EXCESS   = 2_000.0
    _CRIT_EXCESS   = 4_000.0

    _ALERT_MAP = {
        "DENSITY_ALT_WARN": {
            "id": "DENSITY_ALT", "severity": "warning",
            "msg": "HIGH DENSITY ALTITUDE",
            "detail": "Density altitude 2000 ft above pressure altitude — reduced performance",
        },
        "DENSITY_ALT_CRIT": {
            "id": "DENSITY_ALT", "severity": "critical",
            "msg": "EXTREME DENSITY ALTITUDE",
            "detail": "Density altitude 4000+ ft above pressure alt — significantly degraded performance",
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

        alt   = state.get("alt_captain", 0.0)
        oat   = state.get("oat_c", 15.0)
        isa_c = 15.0 - (alt / 1_000.0) * 1.98
        density_alt = alt + 120.0 * (oat - isa_c)
        excess      = density_alt - alt

        state["density_alt_ft"] = round(density_alt, 0)
        state["isa_dev_c"]      = round(oat - isa_c, 1)

        if phase not in self._ACTIVE_PHASES:
            if self._last_alert:
                await self._clear()
            return

        if excess > self._CRIT_EXCESS:
            alert = "DENSITY_ALT_CRIT"
        elif excess > self._WARN_EXCESS:
            alert = "DENSITY_ALT_WARN"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  density_alt={density_alt:.0f}  press_alt={alt:.0f}  excess={excess:.0f} ft  OAT={oat:.1f}°C")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] DENSITY_ALT CLEAR — within normal range")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "DENSITY_ALT"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "DENSITY_ALT"})
