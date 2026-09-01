import random


class HoldingPatternInjector:
    """Burns fuel at holding-power flow rate for a set duration, simulating
    an unplanned ATC hold. Unlike fuel_leak, this raises
    fuel_flow_total_lbs_hr to match the drain — legitimate consumption, not
    a leak — so it's the existing FUEL_EXHAUSTION / MIN_DIVERT_FUEL /
    endurance modules that end up alerting, not a false FUEL_LEAK.

    Terminal commands
        inject holding [duration_min] [rate_lbs_hr]
        clear holding
    """

    NAME = "Holding Pattern"

    def __init__(self):
        self.rate_lbs_hr = 0.0
        self.duration_s  = 0.0
        self._elapsed    = 0.0
        self.active      = False

    def start(self, gen, duration_s: float | None = None, rate_lbs_hr: float | None = None) -> None:
        if rate_lbs_hr is not None:
            self.rate_lbs_hr = max(0.0, rate_lbs_hr)
        else:
            self.rate_lbs_hr = max(0.0, gen.perf.ff_holding_lbs_hr * gen.perf.engine_count)
        self.duration_s = max(0.0, duration_s if duration_s is not None else random.uniform(300.0, 1500.0))
        self._elapsed = 0.0
        self.active = self.rate_lbs_hr > 0 and self.duration_s > 0
        if self.active:
            print(f"[INJECT] holding  rate={self.rate_lbs_hr:.0f} lbs/hr  duration={self.duration_s / 60:.1f} min")

    def stop(self) -> None:
        self.active   = False
        self._elapsed = 0.0
        print("[INJECT] holding cleared")

    def __call__(self, state: dict, gen) -> None:
        if not self.active:
            return

        self._elapsed += gen.dt
        if self._elapsed > self.duration_s:
            self.stop()
            return

        drain_lbs = self.rate_lbs_hr * gen.dt / 3600.0
        total = gen.fuel_left_lbs + gen.fuel_right_lbs + gen.fuel_center_lbs
        if total > 0:
            for tank in ("left", "right", "center"):
                attr = f"fuel_{tank}_lbs"
                cur = getattr(gen, attr)
                setattr(gen, attr, max(0.0, cur - drain_lbs * cur / total))

        gen.fuel_total_lbs = max(0.0, gen.fuel_left_lbs + gen.fuel_right_lbs + gen.fuel_center_lbs)
        if gen._initial_fuel_lbs > 0:
            gen.fuel = gen.fuel_total_lbs / gen._initial_fuel_lbs * 100.0

        state["fuel_total_lbs"]  = round(gen.fuel_total_lbs,  1)
        state["fuel_left_lbs"]   = round(gen.fuel_left_lbs,   1)
        state["fuel_right_lbs"]  = round(gen.fuel_right_lbs,  1)
        state["fuel_center_lbs"] = round(gen.fuel_center_lbs, 1)
        state["fuel_pct"]        = round(gen.fuel,            2)
        state["fuel_lbs"]        = round(gen.fuel_total_lbs)

        # Real engine burn, not a hidden leak — keep the meter in sync so
        # FuelLeakDetectionModule doesn't mistake this for one.
        state["fuel_flow_total_lbs_hr"] = round(state.get("fuel_flow_total_lbs_hr", 0.0) + self.rate_lbs_hr, 1)
        state["holding"] = True
