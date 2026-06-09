from core.data_bus import DataBus


class DiffPressureModule:
    """PRESS-3 — Differential Pressure Monitor.

    The fuselage is designed for a maximum differential pressure (Δp)
    between cabin and outside.  Exceeding this causes structural risk.
    Typical limits:
      Max positive Δp: 8.6–9.4 psi (type-specific)
      Max negative Δp: −0.5 psi (pushes outward against skin)

    WARN  diff_psi > 8.0  or  diff_psi < −0.3
    CRIT  diff_psi > 8.6  or  diff_psi < −0.5
    """

    _SUPPRESS_PHASES = frozenset({"GROUND_ROLL", "COMPLETE"})

    _ALERT_MAP = {
        "DIFF_PRESS_HIGH_WARN": {
            "id": "DIFF_PRESS", "severity": "warning",
            "msg": "DIFF PRESSURE HIGH",
            "detail": "Cabin differential pressure near limit — check outflow valve",
        },
        "DIFF_PRESS_HIGH_CRIT": {
            "id": "DIFF_PRESS", "severity": "critical",
            "msg": "DIFF PRESSURE LIMIT EXCEEDED",
            "detail": "Max differential pressure exceeded — structural risk, open dump valve",
        },
        "DIFF_PRESS_NEG_WARN": {
            "id": "DIFF_PRESS", "severity": "warning",
            "msg": "NEGATIVE DIFF PRESSURE",
            "detail": "Negative differential pressure — check pressurization pack",
        },
        "DIFF_PRESS_NEG_CRIT": {
            "id": "DIFF_PRESS", "severity": "critical",
            "msg": "NEGATIVE DIFF PRESSURE — STRUCTURAL RISK",
            "detail": "Negative Δp at limit — cabin pushing outward on fuselage skin",
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

        dp = state.get("cabin_diff_psi", 0.0)

        if dp > 8.6:
            alert = "DIFF_PRESS_HIGH_CRIT"
        elif dp > 8.0:
            alert = "DIFF_PRESS_HIGH_WARN"
        elif dp < -0.5:
            alert = "DIFF_PRESS_NEG_CRIT"
        elif dp < -0.3:
            alert = "DIFF_PRESS_NEG_WARN"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  diff_psi={dp:.2f}  phase={phase}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] DIFF_PRESS CLEAR — pressure differential normal")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "DIFF_PRESS"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "DIFF_PRESS"})
