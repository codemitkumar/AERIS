from collections import deque
from core.data_bus import DataBus


class OverspeedModule:
    """Monitors IAS and Mach against aircraft-specific VMO/MMO (or VNE for GA).

    Two alert tiers
    ---------------
    APPROACHING OVERSPEED (warning)
        Fires when ANY of the following is true:
          • IAS is within MARGIN_KTS of VMO  (default 15 kts)
          • Mach is within MARGIN_MACH of MMO (default 0.02)
          • At the current acceleration rate, VMO will be reached in < 120 s
          • At the current acceleration rate, MMO will be reached in < 120 s
          • GA only: IAS exceeds VNO (yellow-arc entry, structural caution zone)

    OVERSPEED (critical)
        Fires when IAS ≥ VMO or Mach ≥ MMO.

    Acceleration is computed by comparing the mean IAS over two consecutive
    2-second windows (120 ticks at 30 Hz), which reduces the ±1 kt ADC noise
    to ~0.07 kt average error before the rate calculation.

    Speed limits are read from AircraftPerf at construction — each aircraft type
    carries its own certified values (A320: 350 / M0.82, C172P: VNE 163 kts, …).
    """

    # Smoothing window per half-buffer: 2 s × 30 Hz = 60 ticks
    # Two halves are compared → effective dt = 2 s for rate calculation
    _HALF_WIN = 60

    MARGIN_KTS   = 15.0    # kts below VMO  → approaching warning
    MARGIN_MACH  = 0.02    # Mach below MMO → approaching warning
    APPROACH_SEC = 120.0   # seconds-to-exceed threshold

    _ALERT_MAP = {
        "OVERSPEED": {
            "id":       "OVERSPEED",
            "severity": "critical",
            "msg":      "OVERSPEED",
            "detail":   "Aircraft exceeds maximum operating speed — reduce thrust, extend speedbrakes, check pitch",
        },
        "APPROACHING_OVERSPEED": {
            "id":       "OVERSPEED",
            "severity": "warning",
            "msg":      "OVERSPEED CAUTION",
            "detail":   "Approaching maximum operating speed — check throttle, pitch, and descent rate",
        },
    }

    def __init__(self, ws=None, perf=None):
        self._ws = ws

        if perf is not None:
            self._vmo    = perf.vmo_kts
            self._mmo    = perf.mmo        # 0.0 = not applicable
            self._vno    = perf.vno_kts    # 0.0 = not applicable (GA only)
        else:
            # Sensible jet defaults if no perf supplied
            self._vmo = 350.0
            self._mmo = 0.82
            self._vno = 0.0

        self._last_alert: str | None = None

        # Two back-to-back 2-second windows for smooth rate computation
        self._ias_buf:  deque[float] = deque(maxlen=self._HALF_WIN * 2)
        self._mach_buf: deque[float] = deque(maxlen=self._HALF_WIN * 2)

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    # ── tick handler ──────────────────────────────────────────────────────────

    async def on_state(self, state: dict) -> None:
        phase   = state.get("phase", "")
        ias_cap = state.get("ias_captain", 0.0)
        ias_fo  = state.get("ias_fo",      0.0)
        mach    = state.get("mach",        0.0)

        if phase == "COMPLETE":
            await self._clear()
            return

        avg_ias = (ias_cap + ias_fo) / 2.0
        self._ias_buf.append(avg_ias)
        self._mach_buf.append(mach)

        # ── Acceleration from smoothed two-window comparison ──────────────────
        ias_accel  = 0.0   # kts/s
        mach_accel = 0.0   # /s

        if len(self._ias_buf) == self._HALF_WIN * 2:
            half    = self._HALF_WIN
            buf     = list(self._ias_buf)
            old_ias = sum(buf[:half]) / half
            new_ias = sum(buf[half:]) / half
            dt      = half / 30.0          # 2 seconds
            ias_accel = (new_ias - old_ias) / dt

        if self._mmo > 0 and len(self._mach_buf) == self._HALF_WIN * 2:
            half      = self._HALF_WIN
            buf       = list(self._mach_buf)
            old_mach  = sum(buf[:half]) / half
            new_mach  = sum(buf[half:]) / half
            dt        = half / 30.0
            mach_accel = (new_mach - old_mach) / dt

        # ── Headroom ──────────────────────────────────────────────────────────
        ias_headroom  = self._vmo - avg_ias
        mach_headroom = (self._mmo - mach) if self._mmo > 0 else float("inf")

        # ── Time-to-exceed (only meaningful when accelerating) ────────────────
        ias_tte  = ias_headroom  / ias_accel   if ias_accel  > 0.1  else float("inf")
        mach_tte = mach_headroom / mach_accel  if mach_accel > 0.001 else float("inf")

        # ── Alert decision ────────────────────────────────────────────────────
        ias_over  = avg_ias >= self._vmo
        mach_over = self._mmo > 0 and mach >= self._mmo

        ias_approaching = (
            (ias_headroom  < self.MARGIN_KTS  and avg_ias > 100)  # ignore ground roll
            or ias_tte < self.APPROACH_SEC
        )
        mach_approaching = (
            (self._mmo > 0 and mach_headroom < self.MARGIN_MACH)
            or (self._mmo > 0 and mach_tte < self.APPROACH_SEC)
        )
        # GA: entering yellow arc (VNO exceeded) counts as approaching
        vno_approaching = self._vno > 0 and avg_ias > self._vno

        if ias_over or mach_over:
            alert = "OVERSPEED"
        elif ias_approaching or mach_approaching or vno_approaching:
            alert = "APPROACHING_OVERSPEED"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            ctx = self._context(
                avg_ias, mach, ias_headroom, mach_headroom,
                ias_tte, mach_tte, ias_over, mach_over
            )
            print(f"[ALERT] {alert}  {ctx}  phase={phase}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] OVERSPEED CLEAR — speed within limits")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "OVERSPEED"})

    # ── helpers ───────────────────────────────────────────────────────────────

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._last_alert = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "OVERSPEED"})

    def _context(
        self,
        ias: float, mach: float,
        ias_room: float, mach_room: float,
        ias_tte: float, mach_tte: float,
        ias_over: bool, mach_over: bool,
    ) -> str:
        parts = []
        if ias_over:
            parts.append(f"IAS={ias:.1f} kts EXCEEDS VMO={self._vmo:.0f} kts")
        else:
            tte_str = f"TTE={ias_tte:.0f}s" if ias_tte < self.APPROACH_SEC else ""
            parts.append(f"IAS={ias:.1f}/{self._vmo:.0f} kts (Δ{ias_room:.1f} kts {tte_str})".strip())
        if self._mmo > 0:
            if mach_over:
                parts.append(f"M={mach:.3f} EXCEEDS MMO={self._mmo:.3f}")
            else:
                tte_str = f"TTE={mach_tte:.0f}s" if mach_tte < self.APPROACH_SEC else ""
                parts.append(f"M={mach:.3f}/{self._mmo:.3f} (Δ{mach_room:.3f} {tte_str})".strip())
        return "  ".join(parts)
