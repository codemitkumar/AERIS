import collections
from core.data_bus import DataBus


class IceAccumulationModule:
    """ICE-3 — Ice Accumulation Estimator.

    Integrates time spent in icing conditions to estimate ice buildup.
    Ice accumulation degrades aerodynamic performance, increases stall speed,
    and adds weight.

    Accumulation rate: +1 unit/tick in icing cond without anti-ice, +0.2 with.
    Dissipation rate:  −0.5 unit/tick when outside envelope.

    WARN  accumulation > 30 units  (noticeable performance degradation)
    CRIT  accumulation > 60 units  (severe icing — structural and aero risk)
    """

    _SUPPRESS_PHASES = frozenset({"GROUND_ROLL", "COMPLETE"})
    _ACCUM_RATE_FULL = 1.0
    _ACCUM_RATE_AI   = 0.2
    _DISSIP_RATE     = 0.5
    _WARN_LEVEL      = 30.0
    _CRIT_LEVEL      = 60.0
    _OAT_HIGH_C      = 5.0
    _OAT_LOW_C       = -20.0

    _ALERT_MAP = {
        "ICE_ACCUM_WARNING": {
            "id": "ICE_ACCUM", "severity": "warning",
            "msg": "ICE ACCUMULATION",
            "detail": "Significant ice buildup — performance degraded, stall speed increased",
        },
        "ICE_ACCUM_CRITICAL": {
            "id": "ICE_ACCUM", "severity": "critical",
            "msg": "SEVERE ICE ACCUMULATION",
            "detail": "Severe ice — structural and aerodynamic hazard, divert immediately",
        },
    }

    def __init__(self, ws=None, perf=None):
        self._ws    = ws
        self._level = 0.0
        self._last_alert: str | None = None

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        phase = state.get("phase", "")
        if phase == "COMPLETE":
            await self._clear(); return
        if phase in self._SUPPRESS_PHASES:
            return

        oat       = state.get("oat_c", -99.0)
        anti_ice  = state.get("anti_ice", True)
        in_env    = self._OAT_LOW_C <= oat <= self._OAT_HIGH_C

        if in_env:
            self._level += self._ACCUM_RATE_AI if anti_ice else self._ACCUM_RATE_FULL
        else:
            self._level = max(0.0, self._level - self._DISSIP_RATE)

        state["ice_accumulation"] = round(self._level, 1)

        if self._level > self._CRIT_LEVEL:
            alert = "ICE_ACCUM_CRITICAL"
        elif self._level > self._WARN_LEVEL:
            alert = "ICE_ACCUM_WARNING"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  level={self._level:.1f}  OAT={oat:.1f}°C  anti_ice={anti_ice}  phase={phase}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            if self._level <= self._WARN_LEVEL and self._last_alert is None:
                pass  # already cleared
            else:
                print("[ALERT] ICE_ACCUM CLEAR — ice dissipating")
                if self._ws:
                    await self._ws.broadcast_alert({"topic": "alert_clear", "id": "ICE_ACCUM"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._level      = 0.0
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "ICE_ACCUM"})
