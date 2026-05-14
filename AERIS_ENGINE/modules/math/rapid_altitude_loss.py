from collections import deque
from core.data_bus import DataBus


class RapidAltitudeLossModule:
    """Detects sudden large altitude loss consistent with windshear, microburst, or severe downdraft.

    Tracks the peak altitude seen within a rolling 20-second window and alerts when the
    current altitude falls too far below that peak.  This catches fast altitude loss events
    regardless of whether a descent is commanded — a normal approach descent of 500 ft/min
    loses only ~167 ft in 20 s, well below both thresholds.

    Threshold rationale:
      WARN  1 000 ft loss in 20 s  ≈  3 000 ft/min equivalent
      CRIT  2 000 ft loss in 20 s  ≈  6 000 ft/min — microburst / severe windshear territory
    """

    # Rolling history depth (30 Hz × 20 s)
    _WINDOW_TICKS = 600

    WARN_LOSS_FT = 1_000   # ft lost vs window peak
    CRIT_LOSS_FT = 2_000

    # Suppress near the ground where small absolute differences are meaningless
    MIN_ALT_FT = 300

    _ALERT_MAP = {
        "RAPID_ALT_LOSS_WARN": {
            "id":       "RAPID_ALT_LOSS",
            "severity": "warning",
            "msg":      "RAPID ALT LOSS",
            "detail":   "Large altitude loss in 20 s — possible windshear. Check airspeed and apply thrust.",
        },
        "RAPID_ALT_LOSS_CRIT": {
            "id":       "RAPID_ALT_LOSS",
            "severity": "critical",
            "msg":      "WINDSHEAR / ALT LOSS",
            "detail":   "Severe altitude loss — microburst or windshear suspected. Max thrust, level wings, do not change configuration.",
        },
    }

    def __init__(self, ws=None):
        self._ws = ws
        self._last_alert: str | None = None
        # (time, pressure_alt_ft) pairs; maxlen auto-evicts oldest
        self._alt_history: deque[tuple[float, float]] = deque(maxlen=self._WINDOW_TICKS)

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        phase = state.get("phase", "")
        t     = state.get("time", 0.0)
        # pressure_alt_ft is derived directly from true altitude (no ADC noise) — use it
        # for magnitude comparisons; fall back to captain ADC if unavailable
        alt   = state.get("pressure_alt_ft", state.get("alt_captain", 0.0))

        if phase in ("GROUND_ROLL", "ROTATION", "COMPLETE") or alt < self.MIN_ALT_FT:
            self._alt_history.clear()
            if self._last_alert is not None:
                self._last_alert = None
                if self._ws:
                    await self._ws.broadcast_alert({"topic": "alert_clear", "id": "RAPID_ALT_LOSS"})
            return

        self._alt_history.append((t, alt))

        if len(self._alt_history) < 2:
            return

        peak_alt = max(a for _, a in self._alt_history)
        loss_ft  = peak_alt - alt   # positive means we are below the window peak

        if loss_ft >= self.CRIT_LOSS_FT:
            alert = "RAPID_ALT_LOSS_CRIT"
        elif loss_ft >= self.WARN_LOSS_FT:
            alert = "RAPID_ALT_LOSS_WARN"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            window_s = t - self._alt_history[0][0]
            print(
                f"[ALERT] {alert}  loss={loss_ft:.0f}ft over {window_s:.1f}s  "
                f"peak={peak_alt:.0f}ft  now={alt:.0f}ft  phase={phase}"
            )
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] RAPID_ALT_LOSS CLEAR — altitude rate normal")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "RAPID_ALT_LOSS"})
