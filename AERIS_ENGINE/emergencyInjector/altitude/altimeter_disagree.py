class AltimeterDisagreeInjector:
    """Injects a drifting altitude offset on one ADC channel.

    Models a static port blockage, ADC computation fault, or QNH mis-set on one
    side.  The generator's true altitude is untouched — only the instrument reading
    drifts, causing the cross-check module to alert.

    Drift model
    -----------
    Each tick the biased channel grows by `rate` ft.  At 30 Hz and rate=2 the
    displayed altitude drifts ~60 ft/s, reaching a 200 ft disagree in ~3 seconds.

    Terminal commands
    -----------------
        inject alt_disagree captain 2
        inject alt_disagree fo -3
        clear alt_disagree
    """

    NAME = "Altimeter Disagree"

    def __init__(self, side: str = "captain", rate: float = 2.0):
        if side not in ("captain", "fo", "both"):
            raise ValueError(f"side must be 'captain', 'fo', or 'both', got '{side}'")
        self.side   = side
        self.rate   = rate   # ft per tick, positive = over-reading
        self._drift = 0.0
        self.active = False

    def start(self, side: str | None = None, rate: float | None = None) -> None:
        if side is not None:
            if side not in ("captain", "fo", "both"):
                raise ValueError(f"side must be 'captain', 'fo', or 'both', got '{side}'")
            self.side = side
        if rate is not None:
            self.rate = rate
        self._drift = 0.0
        self.active = True
        print(
            f"[INJECT] altimeter_disagree  side={self.side}  "
            f"rate={self.rate:+.1f} ft/tick  (~{self.rate * 30:+.0f} ft/s)"
        )

    def stop(self) -> None:
        self.active = False
        self._drift = 0.0
        print("[INJECT] altimeter_disagree cleared")

    @property
    def drift_ft(self) -> float:
        return self._drift

    def __call__(self, state: dict, gen) -> None:
        if not self.active:
            return
        self._drift += self.rate
        if self.side in ("captain", "both"):
            state["alt_captain"] = state.get("alt_captain", 0.0) + self._drift
        if self.side in ("fo", "both"):
            state["alt_fo"] = state.get("alt_fo", 0.0) + self._drift
