import math


class StructuralGEventInjector:
    """Injects sinusoidal vertical speed oscillations to simulate turbulence G loading.

    Adds a sine wave to VS readings each tick.  The rapid change in VS between
    consecutive ticks represents vertical acceleration (jerk), which the structural
    altitude rate module converts to an equivalent G load.

    The oscillation is also applied to the generator altitude to keep the data
    physically consistent — the net altitude change over a complete cycle is near
    zero, so the aircraft stays at roughly the same level while the G loading spikes.

    Scenario examples
    -----------------
    Severe turbulence  amplitude=600 freq=0.5   (fast, harsh hits)
    Mountain wave      amplitude=400 freq=0.15  (slow, sustained undulation)
    Microburst gust    amplitude=800 freq=0.25  (sharp onset)

    Terminal commands
    -----------------
        inject turbulence               # default 600 ft/min amplitude, 0.25 Hz
        inject turbulence 800 0.5       # custom
        clear turbulence
    """

    NAME = "Structural G / Turbulence"

    def __init__(self, amplitude_fpm: float = 600.0, freq_hz: float = 0.25):
        self.amplitude_fpm = amplitude_fpm  # peak VS perturbation (ft/min)
        self.freq_hz       = freq_hz        # oscillation frequency
        self._t_start: float | None = None  # sim-time when injection began
        self.active        = False

    def start(self, amplitude_fpm: float | None = None, freq_hz: float | None = None) -> None:
        if amplitude_fpm is not None: self.amplitude_fpm = amplitude_fpm
        if freq_hz       is not None: self.freq_hz       = freq_hz
        self._t_start = None
        self.active   = True
        print(
            f"[INJECT] structural_g  amplitude={self.amplitude_fpm:.0f} ft/min  "
            f"freq={self.freq_hz:.2f} Hz"
        )

    def stop(self) -> None:
        self.active   = False
        self._t_start = None
        print("[INJECT] structural_g cleared")

    def __call__(self, state: dict, gen) -> None:
        if not self.active:
            return

        if self._t_start is None:
            self._t_start = gen.time

        elapsed    = gen.time - self._t_start
        vs_inject  = self.amplitude_fpm * math.sin(2.0 * math.pi * self.freq_hz * elapsed)

        # Apply to VS readings so the detection module sees rapid change
        state["vs_captain"] = state.get("vs_captain", 0.0) + vs_inject
        state["vs_fo"]      = state.get("vs_fo",      0.0) + vs_inject

        # Apply to altitude so data is physically consistent (net Δalt ≈ 0 per cycle)
        alt_delta = vs_inject * gen.dt / 60.0
        gen.alt = max(0.0, gen.alt + alt_delta)
        for key in ("alt_captain", "alt_fo", "pressure_alt_ft"):
            if key in state:
                state[key] = max(0.0, state[key] + alt_delta)
