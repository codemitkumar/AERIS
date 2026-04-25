from core.data_bus import DataBus
from modules.math.unreliable_speed import UnreliableSpeedModule


def register_all(bus: DataBus) -> None:
    """Register every active module with the data bus.

    Add new modules here — they start receiving live state automatically
    without touching main.py or the flight loop.

    Example for a new module:
        from modules.math.my_module import MyModule
        MyModule().attach(bus)
    """
    UnreliableSpeedModule().attach(bus)
