from core.data_bus import DataBus


class UnsafeConfigModule:
    """GPWS-4 — Unsafe Landing Configuration (GPWS Mode 4).

    Fires when the aircraft is low and slow without gear/flaps configured
    for landing.  Classic scenario: crew forgot to lower gear.

    Mode 4A: gear not down, flaps not in landing config, < 1 000 ft RA
    Mode 4B: flaps not set for landing while gear is down, < 500 ft RA

    Uses: gear field ('UP'/'TRANSIT'/'DOWN'), flap_deg, radio_alt_ft.
    """

    _SUPPRESS_PHASES = frozenset({"GROUND_ROLL", "ROTATION", "CLIMB", "CRUISE", "COMPLETE"})
    _LANDING_FLAP_DEG = 25.0   # minimum flap considered landing config
    _MODE4A_ALT      = 1_000.0
    _MODE4B_ALT      = 500.0

    _ALERT_MAP = {
        "UNSAFE_CONFIG_GEAR": {
            "id": "UNSAFE_CONFIG", "severity": "critical",
            "msg": "GEAR NOT DOWN",
            "detail": "Approaching runway with gear UP — lower gear immediately",
        },
        "UNSAFE_CONFIG_FLAP": {
            "id": "UNSAFE_CONFIG", "severity": "warning",
            "msg": "FLAPS NOT SET",
            "detail": "Low altitude with insufficient flap — configure for landing or go-around",
        },
    }

    def __init__(self, ws=None, perf=None):
        self._ws = ws
        self._last_alert: str | None = None

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        phase = state.get("phase", "")
        if phase == "COMPLETE":
            await self._clear(); return
        if phase in self._SUPPRESS_PHASES:
            return

        ra      = state.get("radio_alt_ft", state.get("alt_captain", 99999.0))
        gear    = state.get("gear", "UP")
        flap    = state.get("flap_deg", 0.0)

        if ra > self._MODE4A_ALT:
            if self._last_alert:
                await self._clear()
            return

        gear_down  = gear == "DOWN"
        flap_ready = flap >= self._LANDING_FLAP_DEG

        if ra < self._MODE4A_ALT and not gear_down:
            alert = "UNSAFE_CONFIG_GEAR"
        elif ra < self._MODE4B_ALT and gear_down and not flap_ready:
            alert = "UNSAFE_CONFIG_FLAP"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  gear={gear}  flap={flap}°  RA={ra:.0f} ft  phase={phase}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] UNSAFE_CONFIG CLEAR — landing config set")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "UNSAFE_CONFIG"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "UNSAFE_CONFIG"})
