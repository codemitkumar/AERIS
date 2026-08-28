import random

from emergencyInjector.unreliable_airspeed              import UnreliableAirspeedInjector
from emergencyInjector.altitude.altimeter_disagree      import AltimeterDisagreeInjector
from emergencyInjector.altitude.uncommanded_descent     import UncommandedDescentInjector
from emergencyInjector.altitude.rapid_altitude_loss     import RapidAltitudeLossInjector
from emergencyInjector.altitude.energy_bleed            import EnergyBleedInjector
from emergencyInjector.altitude.structural_g_event      import StructuralGEventInjector
from emergencyInjector.fuel.fuel_leak                   import FuelLeakInjector
from emergencyInjector.notam.airport_closure             import NotamInjector


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

    NOTAM injection
    ----------------
    Call configure_notam_injection(probability) once before flight_loop starts.
    Independent of configure_auto_inject — this seeds the flight with airport/
    runway/taxiway closure NOTAMs (see emergencyInjector.notam.airport_closure)
    on the very first tick, since real NOTAMs are known before departure.

    Supported inject commands (parsed by terminal_command_reader in main.py)
    -------------------------------------------------------------------------
    inject ias [captain|fo|both] [rate]                    — IAS ADC drift
    inject alt_disagree [captain|fo|both] [rate]          — altimeter ADC drift (ft/tick)
    inject descent [rate_fpm]                             — uncommanded altitude loss
    inject windshear [rate_fpm] [duration_s]              — short windshear burst
    inject energy [alt_fpm] [spd_kts_s]                  — simultaneous alt+speed bleed
    inject turbulence [amplitude_fpm] [freq_hz]           — sinusoidal VS / G oscillation
    inject fuel_leak [rate_lbs_hr] [left|right|center|all] — tank fuel leak
    inject notam [count]                                  — airport/runway closure NOTAMs
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
        self.notam        = NotamInjector()

        self._all = [
            self.ias,
            self.alt_disagree,
            self.descent,
            self.windshear,
            self.energy,
            self.turbulence,
            self.fuel_leak,
            self.notam,
        ]

        # Accumulates names of every fault that was ever activated this flight
        self._ever_injected: set[str] = set()

        # Auto-inject state (configured by configure_auto_inject)
        self._auto_inject_at: float = float("inf")   # sim-time to fire
        self._auto_inject_fn        = None            # lambda that calls inj.start(...)
        self._auto_injected: bool   = False

        # NOTAM auto-inject state (configured by configure_notam_injection)
        self._notam_armed: bool             = False
        self._notam_fired: bool             = False
        self._notam_count_range: tuple      = (10, 10)
        self._notam_corridor_nm: float      = 60.0

    # ── emergency_fn interface ────────────────────────────────────────────────

    def __call__(self, state: dict, gen) -> None:
        """Called by FlightGenerator.step() on every tick."""

        # NOTAMs are briefed pre-departure, so fire on the very first tick
        if self._notam_armed and not self._notam_fired:
            self.notam.start(
                gen,
                count=random.randint(*self._notam_count_range),
                corridor_nm=self._notam_corridor_nm,
            )
            self._notam_fired = True

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

    def configure_notam_injection(
        self,
        probability: float = 0.7,
        count_range: tuple = (10, 10),
        corridor_nm: float = 60.0,
    ) -> bool:
        """Pre-roll whether this flight carries NOTAM airport/runway closures.

        Independent of configure_auto_inject — real flights almost always have
        *some* NOTAMs in effect somewhere on the route or system-wide, so the
        default probability here is high compared to the system-fault roll.
        Defaults to a fixed 10 closures per flight.

        Parameters
        ----------
        probability : float
            Chance (0–1) that NOTAM closures are seeded this flight.
        count_range : tuple[int, int]
            Min/max number of airports that get a closure NOTAM.
        corridor_nm : float
            Lateral distance from the direct origin→destination route within
            which an airport counts as "on path" rather than "scattered".

        Returns
        -------
        bool
            True if NOTAM closures were armed, False if the roll missed.
        """
        self._notam_fired = False

        if random.random() >= probability:
            self._notam_armed = False
            return False

        self._notam_armed        = True
        self._notam_count_range  = count_range
        self._notam_corridor_nm  = corridor_nm
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
