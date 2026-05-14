from collections import deque
from core.data_bus import DataBus

_FT_MIN_TO_MS = 0.3048 / 60.0   # ft/min → m/s
_G_MS2        = 9.81             # m/s²


class StructuralAltitudeRateModule:
    """Monitors altitude rate of change and G loading for structural integrity protection.

    Two complementary checks:

    1. Direct load factor  — state supplies load_factor_g computed from bank angle.
       Catches high-G turns.

    2. Vertical jerk  — rapid change in vertical speed implies a pitch-maneuver
       acceleration that loads the airframe independently of bank angle.
       Computed by comparing the mean VS over consecutive 1-second windows so that
       the ±50 ft/min ADC noise averages out (σ → ~9 ft/min after 30 samples),
       leaving only real attitude changes detectable.

    Phase-transition guard: jerk is suppressed for 2 seconds after any phase change
    because the simulator transitions VS instantaneously (e.g. climb→cruise), which
    would otherwise produce a spurious critical alert on every transition.

    Structural limits referenced (FAR 25 transport category):
      Positive  2.5 g  (ultimate 3.75 g)
      Negative -1.0 g  (ultimate -1.5 g)
    Warnings trigger at 72 % of limit to give crews reaction time.
    """

    # ── Smoothing ─────────────────────────────────────────────────────────────
    _SMOOTH_N = 30       # samples per window = 1 second at 30 Hz
    _PHASE_GUARD_S = 2.0 # seconds to suppress jerk after a phase change

    # ── Load-factor G thresholds ──────────────────────────────────────────────
    G_POS_WARN  =  1.8
    G_POS_CRIT  =  2.2
    G_NEG_WARN  = -0.3
    G_NEG_CRIT  = -0.7

    # ── Vertical jerk thresholds [g] — normalized ΔVS / Δt / g ──────────────
    # A 0.15 g jerk ≈ VS changing 290 ft/min every second — firm but not dangerous
    # A 0.35 g jerk ≈ VS changing 675 ft/min every second — severe turbulence territory
    JERK_WARN_G = 0.15
    JERK_CRIT_G = 0.35

    _ALERT_MAP = {
        "G_EXCEED_WARN": {
            "id":       "G_LIMIT",
            "severity": "warning",
            "msg":      "G LIMIT CAUTION",
            "detail":   "Load factor elevated — reduce bank angle and maneuver rate to protect structure",
        },
        "G_EXCEED_CRIT": {
            "id":       "G_LIMIT",
            "severity": "critical",
            "msg":      "G LIMIT EXCEEDED",
            "detail":   "Load factor near structural limit — risk of airframe damage, level immediately",
        },
        "NEG_G_WARN": {
            "id":       "NEG_G",
            "severity": "warning",
            "msg":      "NEG G CAUTION",
            "detail":   "Negative G detected — fuel starvation and engine flameout risk",
        },
        "NEG_G_CRIT": {
            "id":       "NEG_G",
            "severity": "critical",
            "msg":      "NEG G LIMIT",
            "detail":   "Severe negative G — approaching structural negative limit, ease back gently",
        },
        "ALT_JERK_WARN": {
            "id":       "ALT_JERK",
            "severity": "warning",
            "msg":      "ALT RATE CHANGE",
            "detail":   "Rapid vertical speed change — pitch-maneuver G loading detected, smooth inputs only",
        },
        "ALT_JERK_CRIT": {
            "id":       "ALT_JERK",
            "severity": "critical",
            "msg":      "ALT JERK SEVERE",
            "detail":   "Severe vertical acceleration — structural overstress risk, reduce pitch rate immediately",
        },
    }

    def __init__(self, ws=None):
        self._ws = ws
        self._last_alert: str | None = None
        # Rolling buffer holds two consecutive 1-second windows back-to-back
        self._vs_buf: deque[float] = deque(maxlen=self._SMOOTH_N * 2)
        self._prev_phase: str = ""
        self._phase_change_t: float = -999.0

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        phase  = state.get("phase", "")
        t      = state.get("time", 0.0)
        load_g = state.get("load_factor_g", 1.0)
        vs_cap = state.get("vs_captain", 0.0)
        vs_fo  = state.get("vs_fo",      0.0)

        # Track phase changes so the jerk guard can suppress transient spikes
        if phase != self._prev_phase:
            self._prev_phase     = phase
            self._phase_change_t = t
            self._vs_buf.clear()

        if phase in ("GROUND_ROLL", "ROTATION", "COMPLETE"):
            if self._last_alert is not None:
                self._last_alert = None
                for aid in ("G_LIMIT", "NEG_G", "ALT_JERK"):
                    if self._ws:
                        await self._ws.broadcast_alert({"topic": "alert_clear", "id": aid})
            return

        avg_vs_fpm = (vs_cap + vs_fo) / 2.0
        self._vs_buf.append(avg_vs_fpm)

        alert    = None
        jerk_g   = 0.0
        phase_stable = (t - self._phase_change_t) > self._PHASE_GUARD_S

        # ── Check 1: vertical jerk from smoothed VS ───────────────────────────
        # Only meaningful once both windows are fully populated and phase is stable
        if phase_stable and len(self._vs_buf) == self._SMOOTH_N * 2:
            half = self._SMOOTH_N
            buf  = list(self._vs_buf)
            vs_old_ms = (sum(buf[:half]) / half) * _FT_MIN_TO_MS
            vs_new_ms = (sum(buf[half:]) / half) * _FT_MIN_TO_MS
            dt_s      = half / 30.0   # 1 second
            jerk_g    = abs(vs_new_ms - vs_old_ms) / dt_s / _G_MS2

            if jerk_g >= self.JERK_CRIT_G:
                alert = "ALT_JERK_CRIT"
            elif jerk_g >= self.JERK_WARN_G:
                alert = "ALT_JERK_WARN"

        # ── Check 2: direct load factor — overrides jerk if G is the bigger concern ─
        if load_g <= self.G_NEG_CRIT:
            alert = "NEG_G_CRIT"
        elif load_g <= self.G_NEG_WARN and alert != "NEG_G_CRIT":
            alert = "NEG_G_WARN"
        elif load_g >= self.G_POS_CRIT and alert not in ("NEG_G_CRIT", "NEG_G_WARN"):
            alert = "G_EXCEED_CRIT"
        elif load_g >= self.G_POS_WARN and alert not in ("NEG_G_CRIT", "NEG_G_WARN", "G_EXCEED_CRIT"):
            alert = "G_EXCEED_WARN"

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(
                f"[ALERT] {alert}  load_g={load_g:.3f}g  jerk={jerk_g:.3f}g  "
                f"vs={avg_vs_fpm:.0f} ft/min  phase={phase}"
            )
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] STRUCTURAL OK — G and altitude rate within limits")
            for aid in ("G_LIMIT", "NEG_G", "ALT_JERK"):
                if self._ws:
                    await self._ws.broadcast_alert({"topic": "alert_clear", "id": aid})
