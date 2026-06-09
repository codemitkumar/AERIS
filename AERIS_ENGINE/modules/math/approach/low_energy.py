from core.data_bus import DataBus


class LowEnergyModule:
    """APPR-2 — Low Energy State on Approach.

    Combines low speed AND high sink rate below 500 ft — a precursor to
    Controlled Flight Into Terrain (CFIT) and the classic "black-hole" approach.
    Energy = speed (kinetic) + altitude (potential); losing both simultaneously
    leaves no recovery margin.

    WARN  IAS < VREF + 5  AND  VS < −600 ft/min  below 500 ft
    CRIT  IAS < VREF      AND  VS < −800 ft/min  below 300 ft
    """

    _MAX_ALT_WARN = 500.0
    _MAX_ALT_CRIT = 300.0

    _ALERT_MAP = {
        "LOW_ENERGY_WARNING": {
            "id": "LOW_ENERGY", "severity": "warning",
            "msg": "LOW ENERGY STATE",
            "detail": "Low speed and high sink rate on short final — add thrust",
        },
        "LOW_ENERGY_CRITICAL": {
            "id": "LOW_ENERGY", "severity": "critical",
            "msg": "LOW ENERGY — GO AROUND",
            "detail": "Critically low energy below 300 ft — full thrust, rotate, go-around",
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
        if phase != "DESCENT":
            if self._last_alert:
                await self._clear()
            return

        alt  = state.get("alt_captain", 9999.0)
        vref = state.get("vref_kts", 0.0)
        ias  = (state.get("ias_captain", 0.0) + state.get("ias_fo", 0.0)) / 2.0
        vs   = (state.get("vs_captain", 0.0) + state.get("vs_fo", 0.0)) / 2.0

        if vref <= 0 or alt > self._MAX_ALT_WARN:
            if self._last_alert:
                await self._clear()
            return

        if alt < self._MAX_ALT_CRIT and ias < vref and vs < -800.0:
            alert = "LOW_ENERGY_CRITICAL"
        elif alt < self._MAX_ALT_WARN and ias < vref + 5.0 and vs < -600.0:
            alert = "LOW_ENERGY_WARNING"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  IAS={ias:.0f}  VREF={vref:.0f}  VS={vs:.0f}  alt={alt:.0f} ft")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] LOW_ENERGY CLEAR — adequate energy state")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "LOW_ENERGY"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "LOW_ENERGY"})
