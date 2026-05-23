import random

from emergencyInjector.unreliable_airspeed              import UnreliableAirspeedInjector
from emergencyInjector.altitude.altimeter_disagree      import AltimeterDisagreeInjector
from emergencyInjector.altitude.uncommanded_descent     import UncommandedDescentInjector
from emergencyInjector.altitude.rapid_altitude_loss     import RapidAltitudeLossInjector
from emergencyInjector.altitude.energy_bleed            import EnergyBleedInjector
from emergencyInjector.altitude.structural_g_event      import StructuralGEventInjector
from emergencyInjector.fuel.fuel_leak                   import FuelLeakInjector


class InjectionManager:
    """Composes all fault injectors into a single emergency_fn for FlightGenerator.

    Pass an InjectionManager instance as `emergency_fn` when constructing
    FlightGenerator.  The manager dispatches each tick to every active injector
    and accumulates a record of which emergencies were ever triggered, so the
    final output JSON can carry an accurate 'injectedEmergency' metadata field.

    Auto-inject
    -----------
    Call configure_auto_inject(probability) once before flight_loop starts.
    The manager pre-rolls the decision and schedules the fault to fire at a
    random point during cruise/climb without any external trigger needed.

    Supported inject commands (parsed by terminal_command_reader in main.py)
    -------------------------------------------------------------------------
    inject ias [captain|fo|both] [rate]                    — IAS ADC drift
    inject alt_disagree [captain|fo|both] [rate]          — altimeter ADC drift (ft/tick)
    inject descent [rate_fpm]                             — uncommanded altitude loss
    inject windshear [rate_fpm] [duration_s]              — short windshear burst
    inject energy [alt_fpm] [spd_kts_s]                  — simultaneous alt+speed bleed
    inject turbulence [amplitude_fpm] [freq_hz]           — sinusoidal VS / G oscillation
    inject fuel_leak [rate_lbs_hr] [left|right|center|all] — tank fuel leak
    clear [type|all]                                      — stop one or all injectors
    status                                                — print active injector states
    """

    def __init__(self):
        self.ias          = UnreliableAirspeedInjector()
        self.alt_disagree = AltimeterDisagreeInjector()
        self.descent      = UncommandedDescentInjector()
        self.windshear    = RapidAltitudeLossInjector()
        self.energy       = EnergyBleedInjector()
        self.turbulence   = StructuralGEventInjector()
        self.fuel_leak    = FuelLeakInjector()

        self._all = [
            self.ias,
            self.alt_disagree,
            self.descent,
            self.windshear,
            self.energy,
            self.turbulence,
            self.fuel_leak,
        ]

        # Accumulates names of every fault that was ever activated this flight
        self._ever_injected: set[str] = set()

        # Auto-inject state (configured by configure_auto_inject)
        self._auto_inject_at: float = float("inf")   # sim-time to fire
        self._auto_inject_fn        = None            # lambda that calls inj.start(...)
        self._auto_injected: bool   = False

    # ── emergency_fn interface ────────────────────────────────────────────────

    def __call__(self, state: dict, gen) -> None:
        """Called by FlightGenerator.step() on every tick."""

        # Fire scheduled auto-inject when sim time crosses the threshold
        if not self._auto_injected and gen.time >= self._auto_inject_at:
            self._auto_inject_fn()
            self._auto_injected = True

        for inj in self._all:
            if inj.active:
                self._ever_injected.add(inj.NAME)
            inj(state, gen)

    # ── auto-inject API ───────────────────────────────────────────────────────

    def configure_auto_inject(self, probability: float = 0.05) -> bool:
        """Pre-roll the fault decision for this simulation.

        Parameters
        ----------
        probability : float
            Chance (0–1) that a fault will be injected.  Default 5 %.

        Returns
        -------
        bool
            True if a fault was scheduled, False if the roll missed.
        """
        self._auto_injected = False

        if random.random() >= probability:
            self._auto_inject_at = float("inf")
            self._auto_inject_fn = None
            return False

        # Pick a random injector and randomise its parameters
        self._auto_inject_fn = random.choice([
            lambda: self.ias.start(
                side=random.choice(["captain", "fo", "both"]),
                rate=random.choice([-0.05, -0.10, -0.15, 0.05]),
            ),
            lambda: self.alt_disagree.start(
                side=random.choice(["captain", "fo"]),
                rate=random.uniform(1.5, 4.0),
            ),
            lambda: self.descent.start(
                rate_fpm=random.uniform(400.0, 1200.0),
            ),
            lambda: self.windshear.start(
                rate_fpm=random.uniform(2_500.0, 5_000.0),
                duration_s=random.uniform(10.0, 20.0),
            ),
            lambda: self.energy.start(
                alt_rate_fpm=random.uniform(250.0, 600.0),
                spd_rate_kts_s=random.uniform(0.3, 0.8),
            ),
            lambda: self.turbulence.start(
                amplitude_fpm=random.uniform(400.0, 800.0),
                freq_hz=random.uniform(0.15, 0.5),
            ),
            lambda: self.fuel_leak.start(
                rate_lbs_hr=random.uniform(200.0, 1_000.0),
                tank=random.choice(["left", "right", "center"]),
            ),
        ])

        # Fire somewhere between 60 s and 10 min into the flight (climb / early cruise)
        self._auto_inject_at = random.uniform(60.0, 600.0)
        return True

    # ── metadata ─────────────────────────────────────────────────────────────

    @property
    def injected_summary(self) -> str | None:
        """Human-readable list of all emergencies activated this flight, or None."""
        if not self._ever_injected:
            return None
        return " / ".join(sorted(self._ever_injected))

    # ── control helpers ───────────────────────────────────────────────────────

    def clear_all(self) -> None:
        for inj in self._all:
            if inj.active:
                inj.stop()

    def status(self, gen) -> str:
        lines = [f"[STATUS] phase={gen.phase.name}"]
        for inj in self._all:
            tag = "ACTIVE" if inj.active else "off"
            lines.append(f"  {inj.NAME:35s} {tag}")
        return "\n".join(lines)
