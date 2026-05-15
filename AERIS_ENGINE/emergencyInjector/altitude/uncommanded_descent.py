class UncommandedDescentInjector:
    """Injects extra altitude loss each tick to simulate thrust decay or severe icing.

    Each tick, `rate_fpm` ft/min of extra descent is subtracted from both the
    generator's internal altitude (so next-tick physics start from the new floor)
    and all altitude fields in the current state dict (so both the ADC readings and
    derived altitudes stay consistent).  VS readings are biased negative to reflect
    the unintended sink rate.

    Throttle is shown reduced to model thrust decay without overriding the autopilot
    loop — just enough to pass a plausible cause to the detection module.

    Causes this models
    ------------------
    Icing build-up, progressive thrust decay, autopilot trim runaway, wind shear
    sustained head-to-tailwind shift, pilot incapacitation.

    Terminal commands
    -----------------
        inject descent 600          # 600 ft/min extra sink
        inject descent 1200         # more severe
        clear descent
    """

    NAME = "Uncommanded Descent"

    _ALT_KEYS = ("alt_captain", "alt_fo", "pressure_alt_ft", "density_alt_ft")

    def __init__(self, rate_fpm: float = 600.0):
        self.rate_fpm = rate_fpm   # ft/min of extra unintended descent
        self.active   = False

    def start(self, rate_fpm: float | None = None) -> None:
        if rate_fpm is not None:
            self.rate_fpm = rate_fpm
        self.active = True
        print(f"[INJECT] uncommanded_descent  rate={self.rate_fpm:.0f} ft/min")

    def stop(self) -> None:
        self.active = False
        print("[INJECT] uncommanded_descent cleared")

    def __call__(self, state: dict, gen) -> None:
        if not self.active:
            return

        extra_ft = self.rate_fpm * gen.dt / 60.0

        # Pull aircraft down in generator so physics next tick starts from here
        gen.alt = max(0.0, gen.alt - extra_ft)

        # Reflect the loss in every altitude reading this tick
        for key in self._ALT_KEYS:
            if key in state:
                state[key] = max(0.0, state[key] - extra_ft)

        # Bias VS downward so the detection module sees the sink rate
        state["vs_captain"] = state.get("vs_captain", 0.0) - self.rate_fpm
        state["vs_fo"]      = state.get("vs_fo",      0.0) - self.rate_fpm

        # Show a partial throttle reduction (simulates decaying thrust)
        throttle_reduction = min(0.25, self.rate_fpm / 4800.0)
        state["throttle_pct"] = max(5.0, state.get("throttle_pct", 100.0) * (1.0 - throttle_reduction))
