from core.data_bus import DataBus
from modules.math.unreliable_speed        import UnreliableSpeedModule
from modules.math.altimeter_cross_check   import AltimeterCrossCheckModule
from modules.math.uncommanded_descent     import UncommandedDescentModule
from modules.math.rapid_altitude_loss     import RapidAltitudeLossModule
from modules.math.energy_state            import EnergyStateModule
from modules.math.structural_altitude_rate import StructuralAltitudeRateModule


def register_all(bus: DataBus, ws=None) -> None:
    """Register every active module with the data bus.

    Each module subscribes once here and receives every flight-state tick
    automatically without touching main.py or the flight loop.
    """
    # ── Speed ─────────────────────────────────────────────────────────────────
    UnreliableSpeedModule(ws=ws).attach(bus)

    # ── Altitude ──────────────────────────────────────────────────────────────
    AltimeterCrossCheckModule(ws=ws).attach(bus)     # module ALT-1: PF/PM cross-check
    UncommandedDescentModule(ws=ws).attach(bus)      # module ALT-2: descent without command
    RapidAltitudeLossModule(ws=ws).attach(bus)       # module ALT-3: windshear / rapid loss
    EnergyStateModule(ws=ws).attach(bus)             # module ALT-4: total energy protection
    StructuralAltitudeRateModule(ws=ws).attach(bus)  # module ALT-5: G-limit / altitude jerk
