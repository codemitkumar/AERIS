import collections
from core.data_bus import DataBus


class EfficiencyTrendModule:
    """ENG-5 — Engine Efficiency Trend Monitor.

    Tracks per-engine efficiency over a rolling window and fires if efficiency
    is consistently below nominal.  Low efficiency means:
      • higher fuel burn for same thrust
      • higher EGT
      • potential seal or compressor degradation

    WARN  engine_eff < 0.95 (−5% efficiency)
    CRIT  engine_eff < 0.88 (−12% efficiency — significant degradation)

    Uses a 60-tick smoothing window so momentary fluctuations don't fire alerts.
    """

    _SUPPRESS_PHASES = frozenset({"GROUND_ROLL", "ROTATION", "COMPLETE"})
    _WINDOW          = 60
    _WARN_EFF        = 0.95
    _CRIT_EFF        = 0.88

    _ALERT_MAP = {
        "ENG_EFF_WARNING": {
            "id": "ENG_EFF", "severity": "warning",
            "msg": "ENGINE EFFICIENCY LOW",
            "detail": "Engine efficiency trending below 95% — monitor fuel burn and EGT",
        },
        "ENG_EFF_CRITICAL": {
            "id": "ENG_EFF", "severity": "critical",
            "msg": "ENGINE EFFICIENCY DEGRADED",
            "detail": "Engine efficiency below 88% — significant degradation, consider diversion",
        },
    }

    def __init__(self, ws=None, perf=None):
        self._ws    = ws
        self._buf1  = collections.deque(maxlen=self._WINDOW)
        self._buf2  = collections.deque(maxlen=self._WINDOW)
        self._last_alert: str | None = None

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        phase = state.get("phase", "")
        if phase == "COMPLETE":
            await self._clear(); return
        if phase in self._SUPPRESS_PHASES:
            return

        eff1 = state.get("engine_eff_eng1", 1.0)
        eff2 = state.get("engine_eff_eng2", 1.0)
        self._buf1.append(eff1)
        self._buf2.append(eff2)

        if len(self._buf1) < self._WINDOW:
            return

        avg1 = sum(self._buf1) / len(self._buf1)
        avg2 = sum(self._buf2) / len(self._buf2)
        worst = min(avg1, avg2)

        if worst < self._CRIT_EFF:
            alert = "ENG_EFF_CRITICAL"
        elif worst < self._WARN_EFF:
            alert = "ENG_EFF_WARNING"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  avg_eff1={avg1:.3f}  avg_eff2={avg2:.3f}  worst={worst:.3f}  phase={phase}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] ENG_EFF CLEAR — efficiency normal")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "ENG_EFF"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "ENG_EFF"})
