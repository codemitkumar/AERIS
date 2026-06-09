from core.data_bus import DataBus


class GoAroundAdvisoryModule:
    """APPR-5 — Go-Around Advisory.

    Aggregates other approach alerts to issue a consolidated go-around
    advisory.  Acts as a meta-alert that fires when any combination of
    the following are simultaneously active in state:
      • unstable approach (glideslope_dots or speed out of limits)
      • low energy below 500 ft
      • crosswind or tailwind beyond limits
      • unsafe configuration

    Rather than reading other alert states, this module independently
    evaluates a combined score and fires its own advisory.

    CRIT  score ≥ 3 simultaneous criteria violated on short final
    """

    _FINAL_ALT_FT = 500.0
    _CRIT_SCORE   = 3

    _ALERT_MAP = {
        "GO_AROUND_ADVISORY": {
            "id": "GO_AROUND", "severity": "critical",
            "msg": "GO AROUND — MANDATORY",
            "detail": "Multiple approach criteria violated — execute go-around per SOPs",
        },
    }

    def __init__(self, ws=None, perf=None):
        self._ws   = ws
        self._vref = perf.vref_kts        if perf else 0.0
        self._max_xw = perf.max_crosswind_kts if perf else 25.0
        self._last_alert: str | None = None

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        phase = state.get("phase", "")
        alt   = state.get("alt_captain", 9999.0)

        if phase == "COMPLETE":
            await self._clear(); return
        if phase != "DESCENT" or alt > self._FINAL_ALT_FT:
            if self._last_alert:
                await self._clear()
            return

        score = 0

        vref = state.get("vref_kts", self._vref)
        ias  = (state.get("ias_captain", 0.0) + state.get("ias_fo", 0.0)) / 2.0
        vs   = (state.get("vs_captain",  0.0) + state.get("vs_fo",  0.0)) / 2.0
        dots = state.get("glideslope_dots", 0.0)
        gear = state.get("gear", "DOWN")
        flap = state.get("flap_deg", 30.0)
        xw   = state.get("crosswind_kts", 0.0)
        tw   = state.get("tailwind_kts",  0.0)

        if vref > 0 and (ias > vref + 15.0 or ias < vref - 5.0):
            score += 1
        if vs < -1_000.0:
            score += 1
        if abs(dots) > 1.5:
            score += 1
        if gear != "DOWN" or flap < 25.0:
            score += 1
        if xw > self._max_xw or tw > 15.0:
            score += 1

        alert = "GO_AROUND_ADVISORY" if score >= self._CRIT_SCORE else None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  score={score}/5  alt={alt:.0f} ft  phase={phase}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] GO_AROUND CLEAR — approach within limits")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "GO_AROUND"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "GO_AROUND"})
