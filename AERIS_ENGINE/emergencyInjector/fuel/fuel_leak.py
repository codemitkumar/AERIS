class FuelLeakInjector:
    """Drains fuel from one or more tanks at a configurable rate.

    The leak bypasses the engine fuel-flow meters — engines report normal
    consumption while the tanks lose more fuel than expected.  This creates
    a measurable discrepancy that FuelLeakDetectionModule exploits:

        actual_missing = initial_lbs - current_total_lbs
        expected_burned = ∫ engine_fuel_flow dt
        leak_estimate   = actual_missing - expected_burned

    Optionally creates a left/right imbalance when a single wing tank is
    targeted, which FuelImbalanceModule will also catch independently.

    Modelled causes
    ---------------
    Fuel-line seal failure, wing tank structural crack, refuelling valve
    leak-by, fuel system component failure, sabotage.

    Rate guide
    ----------
    Slow (detectable after ~10 min):   100–400 lbs/hr
    Moderate (detectable in ~3–5 min):  500–1 000 lbs/hr
    Severe (immediate detection):      1 500–3 000 lbs/hr

    Terminal commands
    -----------------
        inject fuel_leak [rate_lbs_hr] [left|right|center|all]
        clear fuel_leak
    """

    NAME = "Fuel Leak"

    _TANK_CHOICES = ("left", "right", "center", "all")

    def __init__(self):
        self.rate_lbs_hr = 400.0
        self.tank        = "left"
        self.active      = False

    def start(self, rate_lbs_hr: float = 400.0, tank: str = "left") -> None:
        self.rate_lbs_hr = max(0.0, rate_lbs_hr)
        self.tank        = tank if tank in self._TANK_CHOICES else "left"
        self.active      = True
        print(f"[INJECT] fuel_leak  rate={self.rate_lbs_hr:.0f} lbs/hr  tank={self.tank}")

    def stop(self) -> None:
        self.active = False
        print("[INJECT] fuel_leak cleared")

    def __call__(self, state: dict, gen) -> None:
        if not self.active:
            return

        drain_lbs = self.rate_lbs_hr * gen.dt / 3600.0

        # Drain from the target tank(s) on the generator
        if self.tank in ("left", "all"):
            gen.fuel_left_lbs   = max(0.0, gen.fuel_left_lbs   - drain_lbs)
        if self.tank in ("right", "all"):
            gen.fuel_right_lbs  = max(0.0, gen.fuel_right_lbs  - drain_lbs)
        if self.tank in ("center", "all"):
            gen.fuel_center_lbs = max(0.0, gen.fuel_center_lbs - drain_lbs)

        # Recompute total and sync percentage
        gen.fuel_total_lbs = max(0.0, gen.fuel_left_lbs + gen.fuel_right_lbs + gen.fuel_center_lbs)
        if gen._initial_fuel_lbs > 0:
            gen.fuel = gen.fuel_total_lbs / gen._initial_fuel_lbs * 100.0

        # Push updated values into this tick's state dict so detection modules see them
        state["fuel_total_lbs"]  = round(gen.fuel_total_lbs,  1)
        state["fuel_left_lbs"]   = round(gen.fuel_left_lbs,   1)
        state["fuel_right_lbs"]  = round(gen.fuel_right_lbs,  1)
        state["fuel_center_lbs"] = round(gen.fuel_center_lbs, 1)
        state["fuel_pct"]        = round(gen.fuel,            2)
        state["fuel_lbs"]        = round(gen.fuel_total_lbs)
