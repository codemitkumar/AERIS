from core.data_bus import DataBus


class LowSpeedAlertModule:
    """SPD-4 — Low Speed Alert (below VAPP target on approach).

    Active only in DESCENT and LANDING phases when gear is down.

    VAPP target = VREF + 5 kts (standard Airbus/Boeing additive for gusts).
    WARN  IAS < VAPP − 5 kts
    CRIT  IAS < VREF       (below reference speed — add-power immediately)
    """

    _ACTIVE_PHASES = frozenset({"DESCENT", "LANDING"})

    _ALERT_MAP = {
        "LOW_SPEED_WARNING": {
            "id": "LOW_SPEED", "severity": "warning",
            "msg": "LOW SPEED",
            "detail": "IAS below VAPP target — add thrust immediately",
        },
        "LOW_SPEED_CRITICAL": {
            "id": "LOW_SPEED", "severity": "critical",
            "msg": "LOW SPEED — ADD THRUST",
            "detail": "IAS below VREF — full thrust, check configuration, consider go-around",
        },
    }

    def __init__(self, ws=None, perf=None):
        self._ws   = ws
        self._perf = perf
        self._last_alert: str | None = None

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        phase = state.get("phase", "")

        if phase == "COMPLETE":
            await self._clear(); return
        if phase not in self._ACTIVE_PHASES:
            return
        if state.get("gear", "UP") != "DOWN":
            return

        vref = state.get("vref_kts", 0.0)
        if vref <= 0:
            return

        vapp  = vref + 5.0
        ias   = (state.get("ias_captain", 0.0) + state.get("ias_fo", 0.0)) / 2.0

        if ias < vref:
            alert = "LOW_SPEED_CRITICAL"
        elif ias < vapp - 5.0:
            alert = "LOW_SPEED_WARNING"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  IAS={ias:.1f}  VAPP={vapp:.1f}  VREF={vref:.1f}  phase={phase}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] LOW_SPEED CLEAR — speed above VAPP target")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "LOW_SPEED"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "LOW_SPEED"})
