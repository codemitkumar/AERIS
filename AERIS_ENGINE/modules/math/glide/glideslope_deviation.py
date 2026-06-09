from core.data_bus import DataBus


class GlideslopeDeviationModule:
    """GLI-4 — Glideslope Deviation on Approach.

    Computes a simplified 3° ILS glideslope reference altitude based on
    distance to destination, then compares actual altitude.

    Reference: alt_should_ft = dist_to_dest_nm × 318 ft/nm  (3° slope)
    Rule of thumb: 3 nm = ~1 000 ft on a 3° slope.

    Deviation buckets:
      dot_deviation = (actual_alt − should_alt) / 200 ft per dot
      WARN  |deviation| > 1.0 dot   (half a dot above standard ±0.5)
      CRIT  |deviation| > 2.0 dots  (full-scale deviation)

    Active only in DESCENT phase when dist_to_dest_nm ≤ 30 nm and alt < 5 000 ft.
    """

    _WARN_DOTS     = 1.0
    _CRIT_DOTS     = 2.0
    _FT_PER_DOT    = 200.0    # 1 ILS dot ≈ 200 ft at threshold distance
    _FT_PER_NM_3DEG = 318.0   # ft of altitude per nm on 3° slope
    _MAX_DIST_NM   = 30.0
    _MAX_ALT_FT    = 5_000.0

    _ALERT_MAP = {
        "GLIDESLOPE_HIGH": {
            "id": "GLIDESLOPE", "severity": "warning",
            "msg": "GLIDESLOPE HIGH",
            "detail": "Above glideslope — reduce thrust, increase rate of descent",
        },
        "GLIDESLOPE_LOW": {
            "id": "GLIDESLOPE", "severity": "warning",
            "msg": "GLIDESLOPE LOW",
            "detail": "Below glideslope — add thrust, reduce rate of descent",
        },
        "GLIDESLOPE_HIGH_CRIT": {
            "id": "GLIDESLOPE", "severity": "critical",
            "msg": "GLIDESLOPE — FULL SCALE HIGH",
            "detail": "Significantly above glideslope — go-around recommended",
        },
        "GLIDESLOPE_LOW_CRIT": {
            "id": "GLIDESLOPE", "severity": "critical",
            "msg": "GLIDESLOPE — FULL SCALE LOW",
            "detail": "Significantly below glideslope — go-around immediately",
        },
    }

    def __init__(self, ws=None, perf=None):
        self._ws = ws
        self._last_alert: str | None = None

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        phase = state.get("phase", "")
        dist  = state.get("dist_to_dest_nm", 999.0)
        alt   = state.get("alt_captain", 0.0)

        if phase == "COMPLETE":
            await self._clear(); return
        if phase != "DESCENT" or dist > self._MAX_DIST_NM or alt > self._MAX_ALT_FT:
            if self._last_alert:
                await self._clear()
            return

        should_alt = dist * self._FT_PER_NM_3DEG
        deviation_ft = alt - should_alt
        dots = deviation_ft / self._FT_PER_DOT

        state["glideslope_dots"]   = round(dots, 2)
        state["glideslope_alt_ft"] = round(should_alt, 0)

        abs_dots = abs(dots)
        if abs_dots > self._CRIT_DOTS:
            alert = "GLIDESLOPE_HIGH_CRIT" if dots > 0 else "GLIDESLOPE_LOW_CRIT"
        elif abs_dots > self._WARN_DOTS:
            alert = "GLIDESLOPE_HIGH" if dots > 0 else "GLIDESLOPE_LOW"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  alt={alt:.0f}  should={should_alt:.0f}  dots={dots:.2f}  dist={dist:.1f} nm")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] GLIDESLOPE CLEAR — on path")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "GLIDESLOPE"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "GLIDESLOPE"})
