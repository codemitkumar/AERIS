import collections
from core.data_bus import DataBus


class CompressorStallModule:
    """ENG-6 — Compressor Stall Detector.

    A compressor stall produces a rapid, oscillating N1 drop combined with
    a spike in EGT.  Detection:
      - N1 drops > 10 %N1/sec  (rapid uncommanded decrease)
      - AND EGT simultaneously rises > 30 °C vs prior tick
      - OR EGT rises > 20 °C while N1 is declining

    Uses a 2-tick history to measure rate-of-change.

    WARN  one of the above conditions met once
    CRIT  condition sustained > 3 consecutive ticks (stall not recovering)
    """

    _SUPPRESS_PHASES   = frozenset({"GROUND_ROLL", "ROTATION", "COMPLETE"})
    _N1_DROP_RATE      = 10.0   # %N1 per tick
    _EGT_RISE_THRESH   = 30.0   # °C
    _EGT_SOFT_THRESH   = 20.0   # °C (with N1 decline)
    _CRIT_TICKS        = 3

    _ALERT_MAP = {
        "COMP_STALL_WARNING": {
            "id": "COMP_STALL", "severity": "warning",
            "msg": "COMPRESSOR STALL",
            "detail": "Rapid N1 drop with EGT spike — reduce throttle, monitor recovery",
        },
        "COMP_STALL_CRITICAL": {
            "id": "COMP_STALL", "severity": "critical",
            "msg": "SUSTAINED COMPRESSOR STALL",
            "detail": "Stall not self-recovering — engine damage likely, execute engine shutdown checklist",
        },
    }

    def __init__(self, ws=None, perf=None):
        self._ws           = ws
        self._prev_n1      = None
        self._prev_egt     = None
        self._stall_ticks  = 0
        self._last_alert: str | None = None

    def attach(self, bus: DataBus) -> None:
        bus.subscribe(self.on_state)

    async def on_state(self, state: dict) -> None:
        phase = state.get("phase", "")
        if phase == "COMPLETE":
            await self._clear(); return
        if phase in self._SUPPRESS_PHASES:
            return

        n1_list  = state.get("n1_pct",  [])
        egt_list = state.get("egt_c", [])
        if not n1_list or not egt_list:
            return

        n1  = min(n1_list)
        egt = max(egt_list)

        stall_event = False
        if self._prev_n1 is not None:
            dn1  = self._prev_n1  - n1          # positive = drop
            degt = egt - self._prev_egt          # positive = rise
            if dn1 > self._N1_DROP_RATE and degt > self._EGT_RISE_THRESH:
                stall_event = True
            elif dn1 > 0 and degt > self._EGT_SOFT_THRESH:
                stall_event = True

        self._prev_n1  = n1
        self._prev_egt = egt

        if stall_event:
            self._stall_ticks += 1
        else:
            self._stall_ticks = max(0, self._stall_ticks - 1)

        if self._stall_ticks >= self._CRIT_TICKS:
            alert = "COMP_STALL_CRITICAL"
        elif self._stall_ticks > 0:
            alert = "COMP_STALL_WARNING"
        else:
            alert = None

        if alert == self._last_alert:
            return
        self._last_alert = alert

        if alert:
            print(f"[ALERT] {alert}  n1={n1:.1f}  egt={egt:.0f}°C  stall_ticks={self._stall_ticks}  phase={phase}")
            if self._ws:
                await self._ws.broadcast_alert({**self._ALERT_MAP[alert], "topic": "alert"})
        else:
            print("[ALERT] COMP_STALL CLEAR — engines stable")
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "COMP_STALL"})

    async def _clear(self) -> None:
        if self._last_alert is not None:
            self._stall_ticks = 0
            self._last_alert  = None
            if self._ws:
                await self._ws.broadcast_alert({"topic": "alert_clear", "id": "COMP_STALL"})
