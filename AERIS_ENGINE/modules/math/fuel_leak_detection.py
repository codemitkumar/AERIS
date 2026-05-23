from collections import deque
from core.data_bus import DataBus


class FuelLeakDetectionModule:
    """Detects unaccounted fuel loss by comparing engine consumption against tank drain.

    How it works
    ------------
    Engine fuel-flow meters measure only what the engines burn.  A tank leak
    drains fuel *without* going through the engines, so:

        expected_burned  = ∫ fuel_flow_total_lbs_hr · dt   (engine meters)
        actual_missing   = initial_lbs − fuel_total_lbs     (tank gauges)
        leak_estimate    = actual_missing − expected_burned

    If leak_estimate exceeds the warning or critical threshold the module fires
    an alert.  Rate smoothing (30-tick window) prevents ADC noise from triggering
    false positives during normal flight.

    Alert thresholds (percentage of initial fuel load + absolute floor)
    -------------------------------------------------------------------
    WARN : leak > 2 % of initial AND > warn_floor_lbs
    CRIT : leak > 5 % of initial AND > crit_floor_lbs

    The absolute floor prevents false alerts early in the flight when both
    percentages are tiny but random noise may briefly cross 0.

    Suppression
    -----------
    No alerts are fired until expected_burned > 1 % of initial fuel (ensures
    the baseline is established before comparing).  Ground-roll and rotation
    are skipped entirely.
    """

    _SUPPRESS_PHASES = frozenset({"GROUND_ROLL", "ROTATION", "COMPLETE"})

    # Minimum accumulated engine burn before starting comparisons
    _BASELINE_PCT  = 0.01   # 1 % of initial fuel

    # Alert thresholds
    _WARN_PCT      = 0.02   # 2 % of initial → warning
    _CRIT_PCT      = 0.05   # 5 % of initial → critical
    _WARN_FLOOR    = 30.0   # lbs — minimum absolute excess to alert (GA-safe)
    _CRIT_FLOOR    = 70.0   # lbs

    # Smoothing window: 30 ticks × (1/30 s) = 1 s moving average on fuel_total
    _SMOOTH_WIN = 30

    _ALERT_MAP = {
        "FUEL_LEAK_WARNING": {
            "id":       "FUEL_LEAK",
            "severity": "warning",
            "msg":      "FUEL LEAK",
            "detail":   "Fuel loss exceeds engine consumption — check tank gauges and fuel panel",
        },
        "FUEL_LEAK_CRITICAL": {
            "id":       "FUEL_LEAK",
            "severity": "critical",
            "msg":      "FUEL LEAK CRITICAL",
            "detail":   "Severe unaccounted fuel loss — declare emergency, divert, cross-feed check",
        },
    }

    def __init__(self, ws=None):
        self._ws            = ws
        self._initial_lbs   = None
        self._expected_burn = 0.0       # lbs accumulated from engine meters
        self._dt            = 1 / 30    # assumed tick interval
        self._last_alert: str | None = None
        self._fuel_buf: deque[float] = deque(maxlen=self._SMOOTH_WIN)

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        phase = state.get("phase", "")

        if phase == "COMPLETE":
            await self._clear()
            return

        if phase in self._SUPPRESS_PHASES:
            return

        total_lbs = state.get("fuel_total_lbs", 0.0)
        ff_hr     = state.get("fuel_flow_total_lbs_hr", 0.0)

        # Latch the initial fuel on first non-suppressed tick
        if self._initial_lbs is None:
            self._initial_lbs = total_lbs
            return

        # Integrate engine consumption
        self._expected_burn += ff_hr * self._dt / 3600.0

        # Smooth tank reading to suppress gauge noise
        self._fuel_buf.append(total_lbs)
        smooth_total = sum(self._fuel_buf) / len(self._fuel_buf)

        actual_missing = self._initial_lbs - smooth_total
        leak_estimate  = actual_missing - self._expected_burn

        baseline = self._initial_lbs * self._BASELINE_PCT
        if self._expected_burn < baseline:
            return   # too early to distinguish noise from leak

        warn_threshold = max(self._WARN_FLOOR, self._initial_lbs * self._WARN_PCT)
        crit_threshold = max(self._CRIT_FLOOR, self._initial_lbs * self._CRIT_PCT)

        if leak_estimate >= crit_threshold:
            alert = "FUEL_LEAK_CRITICAL"
        elif leak_estimate >= warn_threshold:
            alert = "FUEL_LEAK_WARNING"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        leak_rate_lbs_hr = leak_estimate / (self._expected_burn / max(0.001, ff_hr)) * 3600.0 if ff_hr > 0 else 0.0

        if alert:
            ctx = (
                f"leak_est={leak_estimate:.0f} lbs  "
                f"rate≈{leak_rate_lbs_hr:.0f} lbs/hr  "
                f"expected_burned={self._expected_burn:.0f} lbs  "
                f"actual_missing={actual_missing:.0f} lbs  "
                f"phase={phase}"
            )
            print(f"[ALERT] {alert}  {ctx}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] FUEL_LEAK CLEAR — consumption within expected range")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "FUEL_LEAK"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "FUEL_LEAK"})
