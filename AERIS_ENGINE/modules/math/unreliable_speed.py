from core.data_bus import DataBus


class UnreliableSpeedModule:
    """Detects unreliable airspeed by comparing captain/FO IAS against groundspeed.

    Attach to the DataBus once at startup; it receives live state every tick.
    """

    IAS_DELTA_THRESHOLD = 10.0   # kts — captain vs FO disagree
    GS_BIAS_THRESHOLD   = 20.0   # kts — one side biased far from groundspeed

    def __init__(self):
        self._last_alert: str | None = None

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        ias_cap = state.get("ias_captain", 0.0)
        ias_fo  = state.get("ias_fo",      0.0)
        gs      = state.get("groundspeed_kts", 0.0)

        delta    = abs(ias_cap - ias_fo)
        cap_bias = abs(ias_cap - gs)
        fo_bias  = abs(ias_fo  - gs)

        if delta < self.IAS_DELTA_THRESHOLD:
            alert = None
        elif cap_bias > fo_bias + self.GS_BIAS_THRESHOLD:
            alert = "UNRELIABLE_IAS_CAPTAIN"
        elif fo_bias > cap_bias + self.GS_BIAS_THRESHOLD:
            alert = "UNRELIABLE_IAS_FO"
        else:
            alert = "UNRELIABLE_IAS_BOTH"

        if alert != self._last_alert:
            self._last_alert = alert
            if alert:
                print(
                    f"[MATH/unreliable_speed] {alert}  "
                    f"cap={ias_cap:.1f} fo={ias_fo:.1f} gs={gs:.1f} kts  "
                    f"phase={state.get('phase')}"
                )
            else:
                print("[MATH/unreliable_speed] IAS NORMAL")
