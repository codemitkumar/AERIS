from core.data_bus import DataBus


class FuelImbalanceModule:
    """Monitors left / right wing-tank fuel imbalance.

    Alert threshold
    ---------------
    WARN : |left − right| > 1 % of total fuel on board
    CRIT : |left − right| > 3 % of total fuel on board

    The 1 % threshold matches industry practice (e.g. A320 FCOM 1.28.20 triggers
    an ECAM advisory at 1 500 kg difference; as a percentage of typical fuel load
    this is ~2 %).  Using a percentage rather than a fixed lbs value keeps the
    alert relevant across the C172P (240 lbs) and B747 (280 000 lbs).

    The module is suppressed during ground roll, rotation, and for the first
    60 s of flight — normal cross-feed and tank equalization create transient
    imbalances at startup.

    Alert deduplication
    -------------------
    Only fires when the alert level changes (NONE → WARN, WARN → CRIT, CRIT → NONE).
    Prevents alert floods on every tick.
    """

    _SUPPRESS_PHASES = frozenset({"GROUND_ROLL", "ROTATION", "COMPLETE"})
    _STABLE_TIME     = 60.0    # seconds before alerts are enabled

    _WARN_PCT = 0.01   # 1 % of total fuel
    _CRIT_PCT = 0.03   # 3 % of total fuel
    _MIN_FUEL = 50.0   # lbs — don't alert when tanks almost empty (noise dominates)

    _ALERT_MAP = {
        "FUEL_IMBALANCE_WARNING": {
            "id":       "FUEL_IMBALANCE",
            "severity": "warning",
            "msg":      "FUEL IMBALANCE",
            "detail":   "Wing tank imbalance > 1 % of total fuel — check cross-feed valve and fuel panel",
        },
        "FUEL_IMBALANCE_CRITICAL": {
            "id":       "FUEL_IMBALANCE",
            "severity": "critical",
            "msg":      "FUEL IMBALANCE CRITICAL",
            "detail":   "Wing tank imbalance > 3 % of total fuel — open cross-feed, monitor structure",
        },
    }

    def __init__(self, ws=None):
        self._ws         = ws
        self._last_alert: str | None = None
        self._flight_time = 0.0
        self._dt          = 1 / 30

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        phase = state.get("phase", "")

        if phase == "COMPLETE":
            await self._clear()
            return

        if phase in self._SUPPRESS_PHASES:
            self._flight_time = 0.0
            return

        self._flight_time += self._dt

        if self._flight_time < self._STABLE_TIME:
            return

        left  = state.get("fuel_left_lbs",  0.0)
        right = state.get("fuel_right_lbs", 0.0)
        total = state.get("fuel_total_lbs", 0.0)

        if total < self._MIN_FUEL:
            return

        imbalance     = abs(left - right)
        imbalance_pct = imbalance / total if total > 0 else 0.0

        if imbalance_pct >= self._CRIT_PCT:
            alert = "FUEL_IMBALANCE_CRITICAL"
        elif imbalance_pct >= self._WARN_PCT:
            alert = "FUEL_IMBALANCE_WARNING"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        heavy = "LEFT" if left > right else "RIGHT"

        if alert:
            ctx = (
                f"left={left:.0f} lbs  right={right:.0f} lbs  "
                f"imbalance={imbalance:.0f} lbs ({imbalance_pct*100:.1f}%)  "
                f"heavy={heavy}  phase={phase}"
            )
            print(f"[ALERT] {alert}  {ctx}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] FUEL_IMBALANCE CLEAR — tanks within balance limits")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "FUEL_IMBALANCE"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "FUEL_IMBALANCE"})
