from emergencyInjector.unreliable_airspeed              import UnreliableAirspeedInjector
from emergencyInjector.altitude.altimeter_disagree      import AltimeterDisagreeInjector
from emergencyInjector.altitude.uncommanded_descent     import UncommandedDescentInjector
from emergencyInjector.altitude.rapid_altitude_loss     import RapidAltitudeLossInjector
from emergencyInjector.altitude.energy_bleed            import EnergyBleedInjector
from emergencyInjector.altitude.structural_g_event      import StructuralGEventInjector


class InjectionManager:
    """Composes all fault injectors into a single emergency_fn for FlightGenerator.

    Pass an InjectionManager instance as `emergency_fn` when constructing
    FlightGenerator.  The manager dispatches each tick to every active injector
    and accumulates a record of which emergencies were ever triggered, so the
    final output JSON can carry an accurate 'injectedEmergency' metadata field.

    Supported inject commands (parsed by terminal_command_reader in main.py)
    -------------------------------------------------------------------------
    inject ias [captain|fo|both] [rate]          — IAS ADC drift
    inject alt_disagree [captain|fo|both] [rate] — altimeter ADC drift (ft/tick)
    inject descent [rate_fpm]                    — uncommanded altitude loss
    inject windshear [rate_fpm] [duration_s]     — short windshear burst
    inject energy [alt_fpm] [spd_kts_s]          — simultaneous alt+speed bleed
    inject turbulence [amplitude_fpm] [freq_hz]  — sinusoidal VS / G oscillation
    clear [type|all]                             — stop one or all injectors
    status                                       — print active injector states
    """

    def __init__(self):
        self.ias         = UnreliableAirspeedInjector()
        self.alt_disagree = AltimeterDisagreeInjector()
        self.descent     = UncommandedDescentInjector()
        self.windshear   = RapidAltitudeLossInjector()
        self.energy      = EnergyBleedInjector()
        self.turbulence  = StructuralGEventInjector()

        self._all = [
            self.ias,
            self.alt_disagree,
            self.descent,
            self.windshear,
            self.energy,
            self.turbulence,
        ]

        # Accumulates names of every fault that was ever activated this flight
        self._ever_injected: set[str] = set()

    # ── emergency_fn interface ────────────────────────────────────────────────

    def __call__(self, state: dict, gen) -> None:
        """Called by FlightGenerator.step() on every tick."""
        for inj in self._all:
            if inj.active:
                self._ever_injected.add(inj.NAME)
            inj(state, gen)

    # ── metadata ─────────────────────────────────────────────────────────────

    @property
    def injected_summary(self) -> str | None:
        """Comma-separated list of all emergencies activated during this flight,
        or None if nothing was injected."""
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
