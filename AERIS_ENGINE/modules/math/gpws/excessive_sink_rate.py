from core.data_bus import DataBus


class ExcessiveSinkRateModule:
    """GPWS-1 — Excessive Sink Rate (GPWS Mode 1).

    The primary GPWS mode.  At low altitude, a high sink rate is a terrain
    impact predictor.  FAA / ICAO require warning when sink rate is excessive
    relative to radio altitude.

    Threshold scales with height:
      < 2 500 ft RA:  VS < −1 000 ft/min WARN, < −1 500 ft/min CRIT
      < 1 000 ft RA:  VS < −500 ft/min  WARN,  < −800 ft/min  CRIT

    radio_alt_ft field is used (falls back to alt_captain if absent).
    """

    _SUPPRESS_PHASES = frozenset({"GROUND_ROLL", "COMPLETE"})

    _ALERT_MAP = {
        "SINK_RATE_WARNING": {
            "id": "SINK_RATE", "severity": "warning",
            "msg": "SINK RATE",
            "detail": "High sink rate at low altitude — reduce descent rate",
        },
        "SINK_RATE_CRITICAL": {
            "id": "SINK_RATE", "severity": "critical",
            "msg": "PULL UP — SINK RATE",
            "detail": "Extreme sink rate — terrain impact imminent, execute GPWS escape manoeuvre",
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
        if phase in self._SUPPRESS_PHASES:
            return

        ra  = state.get("radio_alt_ft", state.get("alt_captain", 99999.0))
        vs  = (state.get("vs_captain", 0.0) + state.get("vs_fo", 0.0)) / 2.0

        if ra > 2_500.0:
            if self._last_alert:
                await self._clear()
            return

        if ra < 1_000.0:
            warn_vs, crit_vs = -500.0, -800.0
        else:
            warn_vs, crit_vs = -1_000.0, -1_500.0

        if vs < crit_vs:
            alert = "SINK_RATE_CRITICAL"
        elif vs < warn_vs:
            alert = "SINK_RATE_WARNING"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  VS={vs:.0f} ft/min  RA={ra:.0f} ft  phase={phase}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] SINK_RATE CLEAR — descent rate safe")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "SINK_RATE"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "SINK_RATE"})
