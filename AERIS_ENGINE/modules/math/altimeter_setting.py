from core.data_bus import DataBus

_STD_HPA            = 1013.25    # ICAO standard pressure
_FT_PER_HPA         = 27.0       # ~27 ft altitude error per 1 hPa mis-set
_TRANSITION_ALT_FT  = 18_000.0   # below this: set QNH; above: set 1013.25


class AltimeterSettingModule:
    """ALT-6 — Altimeter Calibration / Baro Setting Error.

    One of the most insidious and common errors: the crew forgets to update
    the altimeter subscale when transitioning altitudes, or arrives at a new
    aerodrome with a stale QNH from the previous sector.

    Below the Transition Altitude (18 000 ft, ICAO standard for US/intl):
      • Crew should have local QNH set.
      • Error if baro_setting deviates from qnh_hpa.

    Above the Transition Altitude (in Standard layer):
      • Crew should have 1013.25 hPa (FL standard pressure) set.
      • Error if baro_setting ≠ 1013.25.

    Altitude error: (baro_set − correct) × 27 ft/hPa
      Positive error → aircraft higher than indicated (terrain miss)
      Negative error → aircraft lower than indicated (terrain closer than shown)

    WARN  |alt_error_ft| > 100 ft  (≈ 4 hPa off)
    CRIT  |alt_error_ft| > 300 ft  (≈ 11 hPa off — CFIT / separation risk)

    Cross-check: if Captain and FO baro settings disagree by > 5 hPa,
    an additional warning fires regardless of phase.
    """

    _SUPPRESS_PHASES = frozenset({"COMPLETE"})
    _WARN_ERR_FT     = 100.0
    _CRIT_ERR_FT     = 300.0
    _DISAGREE_HPA    = 5.0

    _ALERT_MAP = {
        "BARO_SET_WARNING": {
            "id": "BARO_SET", "severity": "warning",
            "msg": "ALTIMETER SETTING ERROR",
            "detail": "Altimeter baro setting diverges from correct QNH/STD by >100 ft — recheck subscale",
        },
        "BARO_SET_CRITICAL": {
            "id": "BARO_SET", "severity": "critical",
            "msg": "ALTIMETER SETTING — ALTITUDE UNRELIABLE",
            "detail": "Baro error >300 ft — indicated altitude significantly wrong, CFIT/separation risk",
        },
        "BARO_DISAGREE": {
            "id": "BARO_SET", "severity": "warning",
            "msg": "ALTIMETER DISAGREE CAPT/FO",
            "detail": "Captain and FO altimeter subscales differ >5 hPa — cross-check immediately",
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

        alt           = state.get("alt_captain", 0.0)
        qnh           = state.get("qnh_hpa",  _STD_HPA)
        baro_cap      = state.get("baro_setting_captain_hpa", qnh)
        baro_fo       = state.get("baro_setting_fo_hpa",      qnh)

        # Determine correct setting for current altitude layer
        if alt >= _TRANSITION_ALT_FT:
            correct_hpa = _STD_HPA
            layer       = "STD"
        else:
            correct_hpa = qnh
            layer       = "QNH"

        error_cap_hpa = baro_cap - correct_hpa
        error_fo_hpa  = baro_fo  - correct_hpa
        worst_err_hpa = max(abs(error_cap_hpa), abs(error_fo_hpa))
        alt_error_ft  = worst_err_hpa * _FT_PER_HPA

        disagree_hpa  = abs(baro_cap - baro_fo)

        # Publish derived values for AI modules
        state["baro_error_captain_hpa"] = round(error_cap_hpa, 1)
        state["baro_error_fo_hpa"]      = round(error_fo_hpa,  1)
        state["baro_alt_error_ft"]      = round(alt_error_ft,  0)
        state["baro_layer"]             = layer

        # Priority: CRIT > disagree > WARN
        if alt_error_ft > self._CRIT_ERR_FT:
            alert = "BARO_SET_CRITICAL"
        elif disagree_hpa > self._DISAGREE_HPA:
            alert = "BARO_DISAGREE"
        elif alt_error_ft > self._WARN_ERR_FT:
            alert = "BARO_SET_WARNING"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(
                f"[ALERT] {alert}  "
                f"baro_cap={baro_cap}  baro_fo={baro_fo}  correct={correct_hpa}  "
                f"err={worst_err_hpa:.1f} hPa → {alt_error_ft:.0f} ft  "
                f"layer={layer}  alt={alt:.0f} ft  phase={phase}"
            )
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] BARO_SET CLEAR — altimeter settings correct")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "BARO_SET"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "BARO_SET"})
