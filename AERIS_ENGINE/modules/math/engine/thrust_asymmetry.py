from core.data_bus import DataBus


class ThrustAsymmetryModule:
    """ENG-3 — Thrust Asymmetry Monitor.

    Multi-engine aircraft with unequal thrust produce yawing moments.
    Above Vmca (minimum control speed in the air) the rudder can compensate,
    but large asymmetry is a handling hazard.

    Uses N1 spread as thrust proxy (similar to N1_DISAGREE but focuses
    on the control-surface impact and fires at lower thresholds when airborne
    and slow).

    WARN  N1 spread > 6 %  at V < 1.5 × Vs  OR  spread > 12 %  at any speed
    CRIT  N1 spread > 18 % (engine failure level asymmetry)

    Only active when engine_count ≥ 2.
    """

    _SUPPRESS_PHASES = frozenset({"GROUND_ROLL", "COMPLETE"})
    _WARN_SPREAD     = 12.0
    _CRIT_SPREAD     = 18.0
    _LOW_SPEED_SPREAD = 6.0

    _ALERT_MAP = {
        "THRUST_ASYM_WARNING": {
            "id": "THRUST_ASYM", "severity": "warning",
            "msg": "THRUST ASYMMETRY",
            "detail": "Unequal engine thrust detected — rudder input required to maintain heading",
        },
        "THRUST_ASYM_CRITICAL": {
            "id": "THRUST_ASYM", "severity": "critical",
            "msg": "SEVERE THRUST ASYMMETRY",
            "detail": "Major thrust asymmetry — possible engine failure, check EICAS",
        },
    }

    def __init__(self, ws=None, perf=None):
        self._ws           = ws
        self._engine_count = perf.engine_count if perf else 2
        self._vs_kts       = perf.vs_kts       if perf else 120.0
        self._last_alert: str | None = None

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        phase = state.get("phase", "")
        if phase == "COMPLETE":
            await self._clear(); return
        if phase in self._SUPPRESS_PHASES or self._engine_count < 2:
            return

        n1_list = state.get("n1_pct", [])
        if len(n1_list) < 2:
            return

        spread = max(n1_list) - min(n1_list)
        ias    = (state.get("ias_captain", 200.0) + state.get("ias_fo", 200.0)) / 2.0
        low_speed = ias < self._vs_kts * 1.5

        if spread > self._CRIT_SPREAD:
            alert = "THRUST_ASYM_CRITICAL"
        elif spread > self._WARN_SPREAD or (low_speed and spread > self._LOW_SPEED_SPREAD):
            alert = "THRUST_ASYM_WARNING"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  n1={n1_list}  spread={spread:.1f}%  IAS={ias:.0f}  phase={phase}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] THRUST_ASYM CLEAR — thrust balanced")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "THRUST_ASYM"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "THRUST_ASYM"})
