from core.data_bus import DataBus


class GlideRangeModule:
    """GLI-1 — Engine-Out Glide Range Calculator.

    Continuously computes how far the aircraft can glide from current altitude
    assuming all engines fail now (best-glide IAS, no wind correction for
    simplicity).  Broadcasts the range and fires alerts when glide range
    shrinks below survivable minimums.

    Formula: range_nm = alt_ft × L/D  / 6076.12 ft/nm

    WARN  glide_range_nm < 30 nm  (limited diversion options)
    CRIT  glide_range_nm < 10 nm  (immediate landing site required)

    Most relevant in CRUISE / CLIMB phases.  Suppressed on ground.
    """

    _SUPPRESS_PHASES = frozenset({"GROUND_ROLL", "ROTATION", "LANDING", "COMPLETE"})
    _WARN_NM = 30.0
    _CRIT_NM = 10.0
    _FT_PER_NM = 6076.12

    _ALERT_MAP = {
        "GLIDE_RANGE_WARNING": {
            "id": "GLIDE_RANGE", "severity": "warning",
            "msg": "GLIDE RANGE LOW",
            "detail": "Engine-out glide range < 30 nm — identify nearest suitable airfield",
        },
        "GLIDE_RANGE_CRITICAL": {
            "id": "GLIDE_RANGE", "severity": "critical",
            "msg": "GLIDE RANGE CRITICAL",
            "detail": "Engine-out glide range < 10 nm — begin immediate emergency descent",
        },
    }

    def __init__(self, ws=None, perf=None):
        self._ws          = ws
        self._glide_ratio = perf.glide_ratio if perf else 15.0
        self._last_alert: str | None = None

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        phase = state.get("phase", "")

        if phase == "COMPLETE":
            await self._clear(); return
        if phase in self._SUPPRESS_PHASES:
            return

        alt_ft       = state.get("alt_captain", 0.0)
        glide_range  = (alt_ft * self._glide_ratio) / self._FT_PER_NM

        # Publish computed value back into state for AI modules
        state["glide_range_nm"] = round(glide_range, 1)

        if glide_range < self._CRIT_NM:
            alert = "GLIDE_RANGE_CRITICAL"
        elif glide_range < self._WARN_NM:
            alert = "GLIDE_RANGE_WARNING"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  glide_range={glide_range:.1f} nm  alt={alt_ft:.0f} ft  L/D={self._glide_ratio}  phase={phase}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            if glide_range >= self._WARN_NM:
                print(f"[ALERT] GLIDE_RANGE OK — {glide_range:.1f} nm available")
                if self._ws:
                    await self._ws.broadcast_alert({"topic": "alert_clear", "id": "GLIDE_RANGE"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "GLIDE_RANGE"})
