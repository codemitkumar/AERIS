import math
from core.data_bus import DataBus


class TailwindModule:
    """APPR-4 — Tailwind Limit on Approach/Landing.

    Tailwind increases landing distance significantly (≈10 % per 2 kts).
    Maximum tailwind limits are typically 10 kt (wet) / 15 kt (dry).

    tailwind_component = wind_speed × cos(relative_angle) if from behind.

    WARN  tailwind > 10 kts
    CRIT  tailwind > 15 kts

    Active during DESCENT and LANDING phases.
    """

    _ACTIVE_PHASES = frozenset({"DESCENT", "LANDING"})
    _WARN_TW_KTS   = 10.0
    _CRIT_TW_KTS   = 15.0

    _ALERT_MAP = {
        "TAILWIND_WARNING": {
            "id": "TAILWIND", "severity": "warning",
            "msg": "TAILWIND ON APPROACH",
            "detail": "Tailwind > 10 kts — landing distance significantly increased",
        },
        "TAILWIND_CRITICAL": {
            "id": "TAILWIND", "severity": "critical",
            "msg": "TAILWIND LIMIT EXCEEDED",
            "detail": "Tailwind > 15 kts — go-around and request opposite runway or alternate",
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
        if phase not in self._ACTIVE_PHASES:
            if self._last_alert:
                await self._clear()
            return

        wind_spd = state.get("wind_speed_kts", 0.0)
        wind_dir = state.get("wind_dir_deg",  0.0)
        hdg      = state.get("hdg_deg",        0.0)

        relative = (wind_dir - hdg) % 360.0
        if relative > 180.0:
            relative -= 360.0

        # headwind is positive, tailwind is negative of headwind
        headwind   = wind_spd * math.cos(math.radians(relative))
        tailwind   = max(0.0, -headwind)

        state["tailwind_kts"] = round(tailwind, 1)

        if tailwind > self._CRIT_TW_KTS:
            alert = "TAILWIND_CRITICAL"
        elif tailwind > self._WARN_TW_KTS:
            alert = "TAILWIND_WARNING"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  tailwind={tailwind:.1f} kts  phase={phase}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] TAILWIND CLEAR — acceptable wind component")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "TAILWIND"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "TAILWIND"})
