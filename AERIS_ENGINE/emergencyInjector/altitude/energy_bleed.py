class EnergyBleedInjector:
    """Simultaneously decays altitude and airspeed to simulate total energy loss.

    Drains both the potential energy (altitude) and kinetic energy (speed) each tick,
    which pushes the specific-energy metric E = g·h + ½·v² downward.  With both
    channels bleeding, the energy-state module will fire on its dE/dt check.

    Physical analogy: severe icing on wings + engine (high drag + low thrust),
    or extreme density altitude with insufficient thrust margin.

    Terminal commands
    -----------------
        inject energy                     # default 400 ft/min alt, 0.5 kts/s speed
        inject energy 600 0.8             # custom rates
        clear energy
    """

    NAME = "Energy State / Severe Bleed"

    _ALT_KEYS = ("alt_captain", "alt_fo", "pressure_alt_ft", "density_alt_ft")
    _SPD_KEYS = ("ias_captain", "ias_fo", "eas_kts", "cas_kts", "tas_kts", "groundspeed_kts")

    def __init__(self, alt_rate_fpm: float = 400.0, spd_rate_kts_s: float = 0.5):
        self.alt_rate_fpm   = alt_rate_fpm    # ft/min extra altitude loss
        self.spd_rate_kts_s = spd_rate_kts_s  # knots/second speed decay
        self.active         = False

    def start(self, alt_rate_fpm: float | None = None, spd_rate_kts_s: float | None = None) -> None:
        if alt_rate_fpm   is not None: self.alt_rate_fpm   = alt_rate_fpm
        if spd_rate_kts_s is not None: self.spd_rate_kts_s = spd_rate_kts_s
        self.active = True
        print(
            f"[INJECT] energy_bleed  alt={self.alt_rate_fpm:.0f} ft/min  "
            f"spd={self.spd_rate_kts_s:.2f} kts/s"
        )

    def stop(self) -> None:
        self.active = False
        print("[INJECT] energy_bleed cleared")

    def __call__(self, state: dict, gen) -> None:
        if not self.active:
            return

        # ── Altitude drain ───────────────────────────────────────────────────
        alt_delta = self.alt_rate_fpm * gen.dt / 60.0
        gen.alt   = max(0.0, gen.alt - alt_delta)
        for key in self._ALT_KEYS:
            if key in state:
                state[key] = max(0.0, state[key] - alt_delta)
        state["vs_captain"] = state.get("vs_captain", 0.0) - self.alt_rate_fpm
        state["vs_fo"]      = state.get("vs_fo",      0.0) - self.alt_rate_fpm

        # ── Speed drain ──────────────────────────────────────────────────────
        spd_delta  = self.spd_rate_kts_s * gen.dt
        gen.speed  = max(0.0, gen.speed - spd_delta)
        for key in self._SPD_KEYS:
            if key in state:
                state[key] = max(0.0, state[key] - spd_delta)
