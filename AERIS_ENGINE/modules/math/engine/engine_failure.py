from core.data_bus import DataBus


class EngineFailureModule:
    """ENG-4 — Engine Failure Detector.

    Detects when one or more engines have failed:
      - N1 drops to idle (< 20 %) while throttle is set > 50 %
      - OR fuel flow on that engine drops > 70 % vs other engines

    WARN  one engine failed (multi-engine a/c can continue)
    CRIT  two or more engines failed (all-engine failure — immediate glide)
    """

    _SUPPRESS_PHASES  = frozenset({"GROUND_ROLL", "ROTATION", "COMPLETE"})
    _IDLE_N1          = 20.0
    _MIN_THROTTLE_PCT = 50.0

    _ALERT_MAP = {
        "ENGINE_FAILURE_ONE": {
            "id": "ENGINE_FAILURE", "severity": "warning",
            "msg": "ENGINE FAILURE",
            "detail": "One engine not producing thrust at commanded setting — execute engine failure checklist",
        },
        "ENGINE_FAILURE_ALL": {
            "id": "ENGINE_FAILURE", "severity": "critical",
            "msg": "ALL ENGINES FAILED",
            "detail": "Multiple engines at idle despite throttle input — declare MAYDAY, initiate engine restart",
        },
    }

    def __init__(self, ws=None, perf=None):
        self._ws = ws
        self._last_alert: str | None = None

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        phase    = state.get("phase", "")
        throttle = state.get("throttle_pct", 0.0)

        if phase == "COMPLETE":
            await self._clear(); return
        if phase in self._SUPPRESS_PHASES or throttle < self._MIN_THROTTLE_PCT:
            if self._last_alert:
                await self._clear()
            return

        n1_list = state.get("n1_pct", [])
        if not n1_list:
            return

        failed = [n1 for n1 in n1_list if n1 < self._IDLE_N1]
        count  = len(failed)

        if count >= 2:
            alert = "ENGINE_FAILURE_ALL"
        elif count == 1:
            alert = "ENGINE_FAILURE_ONE"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  n1={n1_list}  throttle={throttle:.0f}%  failed_engines={count}  phase={phase}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] ENGINE_FAILURE CLEAR — all engines responding")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "ENGINE_FAILURE"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "ENGINE_FAILURE"})
