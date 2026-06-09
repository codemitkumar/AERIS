from core.data_bus import DataBus


class VrefMonitorModule:
    """GLI-5 — VREF Approach Speed Monitor.

    Monitors speed relative to VREF on final approach (DESCENT phase,
    alt < 3 000 ft, gear down).  Published tolerance is VREF +10/−0 kts.

    WARN  IAS > VREF + 15 kts  (too fast — extended landing distance)
         OR IAS < VREF − 5 kts  (too slow — margin eroded)
    CRIT  IAS > VREF + 25 kts  (dangerously fast for landing)
         OR IAS < VREF          (below reference speed — immediate action)
    """

    _MAX_ALT_FT = 3_000.0

    _ALERT_MAP = {
        "VREF_FAST_WARNING": {
            "id": "VREF", "severity": "warning",
            "msg": "APPROACH SPEED HIGH",
            "detail": "IAS above VREF+15 — reduce speed or plan long landing / go-around",
        },
        "VREF_FAST_CRITICAL": {
            "id": "VREF", "severity": "critical",
            "msg": "APPROACH TOO FAST",
            "detail": "IAS critically above VREF — go-around recommended",
        },
        "VREF_SLOW_WARNING": {
            "id": "VREF", "severity": "warning",
            "msg": "APPROACH SPEED LOW",
            "detail": "IAS below VREF+5 target — add power",
        },
        "VREF_SLOW_CRITICAL": {
            "id": "VREF", "severity": "critical",
            "msg": "BELOW VREF — ADD THRUST",
            "detail": "IAS below reference speed — add full thrust, consider go-around",
        },
    }

    def __init__(self, ws=None, perf=None):
        self._ws   = ws
        self._last_alert: str | None = None

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        phase = state.get("phase", "")
        alt   = state.get("alt_captain", 0.0)
        gear  = state.get("gear", "UP")
        vref  = state.get("vref_kts", 0.0)

        if phase == "COMPLETE":
            await self._clear(); return
        if phase != "DESCENT" or alt > self._MAX_ALT_FT or gear != "DOWN" or vref <= 0:
            if self._last_alert:
                await self._clear()
            return

        ias = (state.get("ias_captain", 0.0) + state.get("ias_fo", 0.0)) / 2.0

        if ias > vref + 25.0:
            alert = "VREF_FAST_CRITICAL"
        elif ias > vref + 15.0:
            alert = "VREF_FAST_WARNING"
        elif ias < vref:
            alert = "VREF_SLOW_CRITICAL"
        elif ias < vref + 5.0:
            alert = "VREF_SLOW_WARNING"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  IAS={ias:.1f}  VREF={vref:.0f}  Δ={ias-vref:+.1f}  alt={alt:.0f} ft")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] VREF CLEAR — speed within limits")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "VREF"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "VREF"})
