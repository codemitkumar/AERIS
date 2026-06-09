from core.data_bus import DataBus


class N1DisagreeModule:
    """ENG-1 — N1 Disagreement between engines.

    Compares N1 readings across all engines.  A significant spread indicates
    partial engine failure, compressor stall, or sensor fault.

    WARN  max(n1) − min(n1) > 8 %N1
    CRIT  max(n1) − min(n1) > 15 %N1

    Suppressed below 30 % N1 (idle / startup) and on ground.
    """

    _SUPPRESS_PHASES = frozenset({"GROUND_ROLL", "ROTATION", "COMPLETE"})
    _IDLE_N1_PCT     = 30.0
    _WARN_SPREAD     = 8.0
    _CRIT_SPREAD     = 15.0

    _ALERT_MAP = {
        "N1_DISAGREE_WARNING": {
            "id": "N1_DISAGREE", "severity": "warning",
            "msg": "N1 DISAGREE",
            "detail": "Engine N1 spread > 8% — check for partial thrust loss or sensor fault",
        },
        "N1_DISAGREE_CRITICAL": {
            "id": "N1_DISAGREE", "severity": "critical",
            "msg": "N1 DISAGREE — ENGINE MALFUNCTION",
            "detail": "N1 spread > 15% — engine failure or severe performance asymmetry suspected",
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

        n1_list = state.get("n1_pct", [])
        if len(n1_list) < 2:
            return
        n1_max = max(n1_list)
        n1_min = min(n1_list)
        if n1_min < self._IDLE_N1_PCT:
            if self._last_alert:
                await self._clear()
            return

        spread = n1_max - n1_min

        if spread > self._CRIT_SPREAD:
            alert = "N1_DISAGREE_CRITICAL"
        elif spread > self._WARN_SPREAD:
            alert = "N1_DISAGREE_WARNING"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  n1={n1_list}  spread={spread:.1f}%N1  phase={phase}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] N1_DISAGREE CLEAR — engines matched")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "N1_DISAGREE"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "N1_DISAGREE"})
