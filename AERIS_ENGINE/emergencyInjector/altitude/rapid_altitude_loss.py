class RapidAltitudeLossInjector:
    """Injects a short-duration severe altitude loss simulating windshear or microburst.

    Active for `duration_s` seconds, then auto-stops.  The generator's altitude is
    drained at `rate_fpm` ft/min for that window, which will trigger both the rapid
    altitude loss module (exceeds window-peak threshold) and the structural G module
    (VS jerk from the sudden onset).

    Typical microburst scenario
    ---------------------------
    4 000–6 000 ft/min downdraft for 10–20 seconds.

    Terminal commands
    -----------------
        inject windshear               # default 4 000 ft/min for 15 s
        inject windshear 6000 20       # custom rate and duration
        clear windshear                # abort early
    """

    NAME = "Rapid Altitude Loss / Windshear"

    _ALT_KEYS = ("alt_captain", "alt_fo", "pressure_alt_ft", "density_alt_ft")

    def __init__(self, rate_fpm: float = 4_000.0, duration_s: float = 15.0):
        self.rate_fpm  = rate_fpm
        self.duration_s = duration_s
        self._elapsed  = 0.0
        self.active    = False

    def start(self, rate_fpm: float | None = None, duration_s: float | None = None) -> None:
        if rate_fpm   is not None: self.rate_fpm   = rate_fpm
        if duration_s is not None: self.duration_s = duration_s
        self._elapsed = 0.0
        self.active   = True
        print(
            f"[INJECT] windshear  rate={self.rate_fpm:.0f} ft/min  "
            f"duration={self.duration_s:.0f}s"
        )

    def stop(self) -> None:
        self.active   = False
        self._elapsed = 0.0
        print("[INJECT] windshear cleared")

    def __call__(self, state: dict, gen) -> None:
        if not self.active:
            return

        self._elapsed += gen.dt
        if self._elapsed > self.duration_s:
            self.stop()
            return

        extra_ft = self.rate_fpm * gen.dt / 60.0
        gen.alt = max(0.0, gen.alt - extra_ft)

        for key in self._ALT_KEYS:
            if key in state:
                state[key] = max(0.0, state[key] - extra_ft)

        state["vs_captain"] = state.get("vs_captain", 0.0) - self.rate_fpm
        state["vs_fo"]      = state.get("vs_fo",      0.0) - self.rate_fpm
