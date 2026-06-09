from core.data_bus import DataBus


class GlideslopeBelowModule:
    """GPWS-5 — Below Glideslope (GPWS Mode 5).

    Distinct from GLI-4 (glideslope_deviation): this module fires the GPWS
    "GLIDESLOPE" callout specifically when the aircraft is significantly below
    the ILS glidepath during approach (the more dangerous direction).

    Uses the glideslope_dots value published by GLI-4 (glideslope_deviation
    module) if present, otherwise computes its own simplified reference.

    WARN  glideslope_dots < −0.5 (below path, soft)
    CRIT  glideslope_dots < −1.5 (well below path — terrain risk)

    Active only during DESCENT, RA < 1 000 ft.
    """

    _MAX_RA_FT   = 1_000.0
    _WARN_DOTS   = -0.5
    _CRIT_DOTS   = -1.5
    _FT_PER_NM   = 318.0
    _FT_PER_DOT  = 200.0

    _ALERT_MAP = {
        "GPWS_GS_WARN": {
            "id": "GPWS_GLIDESLOPE", "severity": "warning",
            "msg": "GLIDESLOPE",
            "detail": "Below ILS glidepath — add thrust and reduce descent rate",
        },
        "GPWS_GS_CRIT": {
            "id": "GPWS_GLIDESLOPE", "severity": "critical",
            "msg": "GLIDESLOPE — GO AROUND",
            "detail": "Significantly below glidepath near terrain — execute go-around immediately",
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
        if phase != "DESCENT":
            if self._last_alert:
                await self._clear()
            return

        ra = state.get("radio_alt_ft", state.get("alt_captain", 99999.0))
        if ra > self._MAX_RA_FT:
            if self._last_alert:
                await self._clear()
            return

        # Prefer pre-computed dots from glideslope_deviation module
        dots = state.get("glideslope_dots")
        if dots is None:
            dist = state.get("dist_to_dest_nm", 5.0)
            alt  = state.get("alt_captain", 0.0)
            should = dist * self._FT_PER_NM
            dots = (alt - should) / self._FT_PER_DOT

        if dots < self._CRIT_DOTS:
            alert = "GPWS_GS_CRIT"
        elif dots < self._WARN_DOTS:
            alert = "GPWS_GS_WARN"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  dots={dots:.2f}  RA={ra:.0f} ft  phase={phase}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] GPWS_GLIDESLOPE CLEAR — on or above path")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "GPWS_GLIDESLOPE"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "GPWS_GLIDESLOPE"})
