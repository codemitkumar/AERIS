import math
from collections import deque
from core.data_bus import DataBus

_G     = 9.81          # m/s²
_FT2M  = 0.3048        # feet  → metres
_KTS2MS = 0.51444      # knots → m/s


def _specific_energy(alt_ft: float, tas_kts: float) -> float:
    """Total specific mechanical energy [J/kg]: E = g·h + ½·v²"""
    h = alt_ft  * _FT2M
    v = tas_kts * _KTS2MS
    return _G * h + 0.5 * v * v


class EnergyStateModule:
    """Monitors total specific energy (E = g·h + ½v²) to detect two dangerous states.

    1. Energy bleed — aircraft simultaneously losing altitude AND airspeed during climb
       or cruise.  This means neither the engine nor the altitude trade is sustaining the
       flight; causes include thrust decay, severe icing, or extreme drag increase.

    2. Low-KE / high-pitch — kinetic energy fraction of total energy drops below a safe
       floor while the nose is held high.  This is the classic pre-stall configuration:
       aircraft is trading the last of its speed for altitude it cannot sustain.

    Specific energy lets AERIS catch these across all altitudes with a single physics
    formula rather than separate speed and altitude comparisons.
    """

    # Rolling window for dE/dt  (30 Hz × 3 s = 90 ticks)
    _ENERGY_WINDOW = 90

    # dE/dt thresholds [J/kg/s] — negative = losing energy
    # 100 J/kg/s ≈ losing the energy equivalent of a 2 000 ft/min descent + speed bleed
    BLEED_WARN_JPKGS = -100
    BLEED_CRIT_JPKGS = -200

    # Stall precursor: kinetic energy is this fraction of total energy
    LOW_KE_FRACTION = 0.10   # 10 %
    HIGH_PITCH_DEG  = 12.0   # degrees nose-up
    MIN_ALT_STALL   = 500    # ft — suppress low-altitude check (TOGA / rotation region)

    # Only the energy-bleed check is conditioned on phase;
    # the low-KE/high-pitch check runs in all airborne phases
    _BLEED_PHASES = frozenset({"CLIMB", "CRUISE"})
    _GROUND_PHASES = frozenset({"GROUND_ROLL", "ROTATION", "LANDING", "COMPLETE"})

    _ALERT_MAP = {
        "ENERGY_BLEED_WARN": {
            "id":       "ENERGY_STATE",
            "severity": "warning",
            "msg":      "ENERGY STATE LOW",
            "detail":   "Aircraft losing total energy — simultaneous altitude and airspeed decay. Check throttle and trim.",
        },
        "ENERGY_BLEED_CRIT": {
            "id":       "ENERGY_STATE",
            "severity": "critical",
            "msg":      "ENERGY STATE CRIT",
            "detail":   "Severe total energy loss — stall or loss of control imminent. Apply thrust, reduce pitch.",
        },
        "LOW_KE_HIGH_PITCH": {
            "id":       "ENERGY_STATE",
            "severity": "critical",
            "msg":      "LOW SPD HIGH PITCH",
            "detail":   "Low kinetic energy with high nose-up pitch — stall precursor. Lower nose and apply thrust.",
        },
    }

    def __init__(self, ws=None):
        self._ws = ws
        self._last_alert: str | None = None
        # (simulation_time, E_sp) pairs for the dE/dt sliding window
        self._energy_buf: deque[tuple[float, float]] = deque(maxlen=self._ENERGY_WINDOW)

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        phase   = state.get("phase", "")
        t       = state.get("time", 0.0)
        alt_ft  = state.get("pressure_alt_ft", state.get("alt_captain", 0.0))
        tas_kts = state.get("tas_kts", state.get("eas_kts", 0.0))
        pitch   = state.get("pitch_deg", state.get("pitch_captain", 0.0))

        if phase in self._GROUND_PHASES:
            self._energy_buf.clear()
            if self._last_alert is not None:
                self._last_alert = None
                if self._ws:
                    await self._ws.broadcast_alert({"topic": "alert_clear", "id": "ENERGY_STATE"})
            return

        E_sp = _specific_energy(alt_ft, tas_kts)
        self._energy_buf.append((t, E_sp))

        alert = None

        # ── Check 1: energy bleed rate (climb / cruise only) ─────────────────
        if phase in self._BLEED_PHASES and len(self._energy_buf) >= 2:
            t0, e0 = self._energy_buf[0]
            t1, e1 = self._energy_buf[-1]
            dt = t1 - t0
            if dt > 0:
                dedt = (e1 - e0) / dt   # J/kg/s
                if dedt <= self.BLEED_CRIT_JPKGS:
                    alert = "ENERGY_BLEED_CRIT"
                elif dedt <= self.BLEED_WARN_JPKGS:
                    alert = "ENERGY_BLEED_WARN"

        # ── Check 2: low-KE high-pitch stall precursor (all airborne phases) ─
        if alert is None and alt_ft > self.MIN_ALT_STALL:
            pe = _G * alt_ft * _FT2M
            v  = tas_kts * _KTS2MS
            ke = 0.5 * v * v
            total = pe + ke
            ke_fraction = ke / total if total > 0 else 0.0
            if ke_fraction < self.LOW_KE_FRACTION and pitch >= self.HIGH_PITCH_DEG:
                alert = "LOW_KE_HIGH_PITCH"

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(
                f"[ALERT] {alert}  E_sp={E_sp:.0f} J/kg  "
                f"alt={alt_ft:.0f}ft  tas={tas_kts:.1f}kts  pitch={pitch:.1f}°  phase={phase}"
            )
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] ENERGY_STATE OK — total energy nominal")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "ENERGY_STATE"})
