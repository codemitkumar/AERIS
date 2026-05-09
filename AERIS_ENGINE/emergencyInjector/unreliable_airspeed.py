class UnreliableAirspeedInjector:
    """Injects a drifting IAS error that accumulates each simulation tick.

    Attach once to FlightGenerator as emergency_fn, then call start()/stop()
    at any point during flight to enable or clear the fault.

    Drift model
    -----------
    Each tick the indicated speed is scaled by (1 + cumulative_drift / 100).
    With rate=-0.1 at 30 Hz the reading drops ~3 % per second, ~18 % per minute.

    Examples
    --------
    Captain pitot icing (default):
        injector = UnreliableAirspeedInjector(side="captain", rate=-0.1)

    FO over-reading:
        injector = UnreliableAirspeedInjector(side="fo", rate=+0.05)

    Both sides failing (total pitot loss scenario):
        injector = UnreliableAirspeedInjector(side="both", rate=-0.1)

    Terminal commands (type while simulation is running):
        inject captain -0.1
        inject fo 0.05
        inject both -0.1
        clear
    """

    def __init__(self, side: str = "captain", rate: float = -0.1):
        """
        Parameters
        ----------
        side : "captain" | "fo" | "both"
        rate : drift added per tick in percent  (negative = under-read)
        """
        if side not in ("captain", "fo", "both"):
            raise ValueError(f"side must be 'captain', 'fo', or 'both', got '{side}'")

        self.side   = side
        self.rate   = rate
        self._drift = 0.0   # accumulated drift in %
        self.active = False

    # ── control ──────────────────────────────────────────────────────────────

    def start(self, side: str | None = None, rate: float | None = None) -> None:
        """Start (or re-configure) the fault injection."""
        if side is not None:
            if side not in ("captain", "fo", "both"):
                raise ValueError(f"side must be 'captain', 'fo', or 'both', got '{side}'")
            self.side = side
        if rate is not None:
            self.rate = rate

        self._drift = 0.0
        self.active = True
        print(
            f"[INJECT] unreliable_airspeed  side={self.side}  "
            f"rate={self.rate:+.2f}%/tick  (~{self.rate * 30:+.1f}%/s)"
        )

    def stop(self) -> None:
        """Clear the fault and reset drift to zero."""
        self.active = False
        self._drift = 0.0
        print("[INJECT] unreliable_airspeed cleared")

    # ── status ───────────────────────────────────────────────────────────────

    @property
    def drift_pct(self) -> float:
        """Current accumulated drift in percent."""
        return self._drift

    # ── emergency_fn interface ────────────────────────────────────────────────

    def __call__(self, state: dict, gen) -> None:
        """Called by FlightGenerator.step() on every tick."""
        if not self.active:
            return

        self._drift += self.rate
        factor = 1.0 + self._drift / 100.0

        if self.side in ("captain", "both"):
            state["ias_captain"] = state["ias_captain"] * factor

        if self.side in ("fo", "both"):
            state["ias_fo"] = state["ias_fo"] * factor
