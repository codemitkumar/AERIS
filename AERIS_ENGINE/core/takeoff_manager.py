"""
AERIS Takeoff Manager.

Runs a physics-accurate takeoff simulation from brake release to cruise
altitude.  Phases:

  GROUND_ROLL   — full TOGA thrust, brakes off, elevator neutral
                  Monitors ground speed until VR.
  ROTATION      — elevator pitched up at rotation_rate_dps until
                  rotation_pitch_deg is reached.
  INITIAL_CLIMB — gear retracted (positive rate confirmed), hold
                  initial_climb_pitch.  Flaps begin retracting at V2+10.
  CLIMB         — pitch for climb_speed_kts (speed-hold P-controller).
                  Flaps fully retracted.  Throttle stays at TOGA.
  LEVELOFF      — entered at cruise_alt - 500 ft.  Pitch reduces to 0,
                  throttle steps down to cruise power.
  CRUISE        — level, trimmed.  Simulation stops and returns summary.

Nothing is hardcoded for any specific aircraft — all values come from
AircraftPerf (including V-speeds computed from first principles).
"""

import asyncio
import math
import time
from enum import Enum, auto
from dataclasses import dataclass, field

from data.ingestion.aircraft_performance import AircraftPerf
from data.ingestion.sensor_layer import SensorLayer, FailureType
from data.ingestion.flight_data_adapter import FlightDataAdapter


# ── Helpers ───────────────────────────────────────────────────────────────────
def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


class Phase(Enum):
    GROUND_ROLL   = auto()
    ROTATION      = auto()
    INITIAL_CLIMB = auto()
    CLIMB         = auto()
    LEVELOFF      = auto()
    CRUISE        = auto()


# ── Takeoff Manager ───────────────────────────────────────────────────────────
class TakeoffManager:
    """
    Parameters
    ----------
    client : JSBSimClient
        Already-initialised client configured for ground start.
    perf   : AircraftPerf
        Populated (V-speeds already computed) performance data.
    ws     : WebSocketServer, optional
        If supplied, state is broadcast each frame.
    dt     : float
        Simulation timestep (seconds). Default 1/60.
    """

    # Pitch P-controller gains  — identical across aircraft because
    # the error is in degrees and the output is normalised (-1…1).
    _KP_PITCH = 0.02
    _KD_PITCH = 0.02   # damps pitch oscillation

    # Speed P-controller  (maps kts error → target pitch offset in deg)
    _KP_SPEED = 0.25

    def __init__(self, client, perf: AircraftPerf, ws=None, dt: float = 1/60):
        self.client = client
        self.perf   = perf
        self.ws     = ws
        self.dt     = dt

        # Phase tracking
        self.phase = Phase.GROUND_ROLL
        self._phase_start_time = 0.0

        # V1/VR/V2 (already on perf)
        self.v1  = perf.v1_kts
        self.vr  = perf.vr_kts
        self.v2  = perf.v2_kts

        # Time at which speed first reached VR (for debounce)
        self._vr_first_seen: float = 0.0

        # Sensor layer + adapter
        self.sensor_layer = SensorLayer()
        self.adapter      = FlightDataAdapter(self.sensor_layer)

        # Event log (speed, altitude, time at each milestone)
        self._log: list[dict] = []

    # ── Failure injection API (called from WS command handler) ───────────────
    def inject_failure(self, side: str, failure_type_str: str,
                       severity: float = 1.0, sim_time: float = 0.0,
                       current_ias: float = 0.0, current_tas: float = 0.0,
                       current_alt: float = 0.0, current_mach: float = 0.0):
        try:
            ft = FailureType(failure_type_str)
        except ValueError:
            print(f"[TakeoffManager] Unknown failure type: {failure_type_str}")
            return
        self.sensor_layer.inject_failure(
            side=side, failure_type=ft, severity=severity,
            sim_time=sim_time, current_ias=current_ias,
            current_tas=current_tas, current_alt=current_alt,
            current_mach=current_mach,
        )

    def clear_failure(self, side: str):
        self.sensor_layer.clear_failure(side)

    # ── Main coroutine ────────────────────────────────────────────────────────
    async def run(self) -> dict:
        """
        Runs the full takeoff to cruise.  Returns a summary dict when done.
        """
        p = self.perf
        print(
            f"\n{'='*60}\n"
            f"  AERIS TAKEOFF SIMULATION — {p.name}\n"
            f"  TOW     : {p.tow_lbs:,.0f} lbs\n"
            f"  V1      : {self.v1:.1f} kts\n"
            f"  VR      : {self.vr:.1f} kts\n"
            f"  V2      : {self.v2:.1f} kts\n"
            f"  Cruise  : FL{int(p.cruise_alt_ft/100):03d}\n"
            f"{'='*60}\n"
        )

        # Configure ground start
        self._setup_ground_start()

        wall_start = time.time()
        prev_pitch = 0.0

        while self.phase != Phase.CRUISE:
            self.client.step()
            state  = self.client.get_state()
            jsb    = state.get("jsbsim",  {})
            der    = state.get("derived", {})
            meta   = state.get("meta",    {})
            engines = state.get("engines", [])

            # ── Read key values ───────────────────────────────────────
            t        = meta.get("sim_time_s") or 0.0
            gs_kts   = (jsb.get("velocities/vg-fps") or 0) * 0.592484
            ias_kts  = jsb.get("velocities/vc-kts") or 0.0
            alt_ft   = jsb.get("position/h-sl-ft")  or 0.0
            agl_ft   = jsb.get("position/h-agl-ft") or alt_ft
            vs_fpm   = (der.get("vertical_speed_fpm") or 0.0)
            pitch    = der.get("pitch_deg") or 0.0
            pitch_rt = der.get("pitch_rate_dps") or 0.0

            # WOW: any gear unit on ground?
            wow = bool(
                jsb.get("gear/unit[0]/WOW") or
                jsb.get("gear/unit[1]/WOW") or
                jsb.get("gear/unit[2]/WOW")
            )

            # ── Phase transitions ─────────────────────────────────────
            self._check_transitions(t, gs_kts, ias_kts, agl_ft, vs_fpm, pitch, wow)

            # ── Control law for current phase ─────────────────────────
            elevator, throttle = self._control_law(
                t, ias_kts, pitch, pitch_rt, agl_ft, wow
            )
            self.client.set_controls(throttle=throttle, elevator=elevator)

            # ── Gear command ──────────────────────────────────────────
            gear_cmd = 1.0
            if self.phase in (Phase.INITIAL_CLIMB, Phase.CLIMB,
                               Phase.LEVELOFF, Phase.CRUISE):
                if not wow and vs_fpm > 50:
                    gear_cmd = 0.0
            try:
                self.client.fdm["gear/gear-cmd-norm"] = gear_cmd
            except Exception:
                pass

            # ── Flap schedule ─────────────────────────────────────────
            self._manage_flaps(ias_kts)

            # ── Sensor layer + adapter ────────────────────────────────
            flight_data = self.adapter.get_data(state, sim_time=t)
            flight_data["phase"]  = self.phase.name
            flight_data["v1_kts"] = self.v1
            flight_data["vr_kts"] = self.vr
            flight_data["v2_kts"] = self.v2

            # ── Broadcast ─────────────────────────────────────────────
            if self.ws:
                await self.ws.broadcast(flight_data)

            # ── Console (once/sec) ────────────────────────────────────
            sim_step = round(t * 60)
            if sim_step % 60 == 0:
                self._print_status(t, self.phase, gs_kts, ias_kts,
                                    alt_ft, agl_ft, vs_fpm, pitch, engines)

            prev_pitch = pitch
            await asyncio.sleep(self.dt)

        # ── Done ──────────────────────────────────────────────────────
        wall_elapsed = time.time() - wall_start
        summary = {
            "model":       p.model,
            "v1_kts":      self.v1,
            "vr_kts":      self.vr,
            "v2_kts":      self.v2,
            "cruise_alt":  p.cruise_alt_ft,
            "wall_time_s": round(wall_elapsed, 1),
            "events":      self._log,
        }
        print(f"\n[AERIS] Reached cruise FL{int(p.cruise_alt_ft/100):03d}. "
              f"Simulation complete in {wall_elapsed:.0f}s wall-time.\n")
        return summary

    # ── Ground start configuration ────────────────────────────────────────────
    def _setup_ground_start(self):
        """Configure JSBSim for a standing-start on the runway."""
        fdm = self.client.fdm
        p   = self.perf

        def _s(prop, val):
            try: fdm[prop] = val
            except Exception: pass

        # Hold on ground with gear down and takeoff flaps.
        # Slight nose-down elevator keeps the nosewheel pressed onto the runway
        # during the idle-run settling phase so the gear contact model stabilises.
        _s("gear/gear-cmd-norm",    1.0)
        _s("fcs/flap-cmd-norm",     p.flap_to_norm)
        _s("fcs/throttle-cmd-norm", 0.0)
        _s("fcs/elevator-cmd-norm", -0.08)   # nose-down to plant gear
        _s("fcs/aileron-cmd-norm",  0.0)
        _s("fcs/rudder-cmd-norm",   0.0)

        # Settle for 5 seconds at idle so gear contact dynamics fully damp out
        for _ in range(300):
            fdm.run()

        # Ramp throttle to TOGA over 1 second to avoid torque shock
        for step in range(1, 61):
            _s("fcs/throttle-cmd-norm", step / 60.0)
            fdm.run()

        self._phase_start_time = 0.0
        self.phase = Phase.GROUND_ROLL
        self._log_event("BRAKE_RELEASE", 0.0, 0.0, 0.0)

    # ── Phase transition logic ────────────────────────────────────────────────
    def _check_transitions(self, t, gs_kts, ias_kts, agl_ft, vs_fpm, pitch, wow):
        p = self.perf

        if self.phase == Phase.GROUND_ROLL:
            if gs_kts >= self.v1 and not self._logged("V1"):
                self._log_event("V1", t, gs_kts, agl_ft)
            if gs_kts >= self.vr:
                if self._vr_first_seen == 0.0:
                    self._vr_first_seen = t
                elif t - self._vr_first_seen >= 0.3:   # sustained for 0.3 s
                    self._transition(Phase.ROTATION, t)
                    self._log_event("VR_REACHED", t, gs_kts, agl_ft)
            else:
                self._vr_first_seen = 0.0   # reset if speed dips below VR

        elif self.phase == Phase.ROTATION:
            if not wow and vs_fpm > 50:
                self._transition(Phase.INITIAL_CLIMB, t)
                self._log_event("LIFTOFF", t, ias_kts, agl_ft)

        elif self.phase == Phase.INITIAL_CLIMB:
            flaps_up = (self.client._flaps <= 0.01)
            above_v2 = ias_kts >= self.v2 + 10
            stable   = agl_ft > 400
            if stable and (above_v2 or flaps_up):
                self._transition(Phase.CLIMB, t)
                self._log_event("CLIMB_START", t, ias_kts, agl_ft)

        elif self.phase == Phase.CLIMB:
            if agl_ft >= p.cruise_alt_ft - 500:
                self._transition(Phase.LEVELOFF, t)
                self._log_event("LEVELOFF_START", t, ias_kts, agl_ft)

        elif self.phase == Phase.LEVELOFF:
            at_alt  = agl_ft >= p.cruise_alt_ft - 50
            level   = abs(vs_fpm) < 200
            if at_alt and level:
                self._transition(Phase.CRUISE, t)
                self._log_event("CRUISE", t, ias_kts, agl_ft)

    def _transition(self, new_phase: Phase, t: float):
        print(f"  [{t:7.1f}s] Phase → {new_phase.name}")
        self.phase = new_phase
        self._phase_start_time = t

    # ── Control laws ──────────────────────────────────────────────────────────
    def _control_law(self, t, ias_kts, pitch, pitch_rt, agl_ft, wow
                     ) -> tuple[float, float]:
        """Returns (elevator_cmd, throttle_cmd)."""
        p = self.perf

        # Throttle
        throttle = 1.0   # TOGA throughout climb
        if self.phase == Phase.LEVELOFF:
            # Gradually reduce to cruise power over 30 s
            elapsed = t - self._phase_start_time
            throttle = _clamp(1.0 - (elapsed / 30.0) * 0.3, 0.70, 1.0)
        elif self.phase == Phase.CRUISE:
            throttle = 0.70

        # Elevator
        elevator = 0.0

        if self.phase == Phase.GROUND_ROLL:
            # Progressively release nose-down pressure as speed approaches VR.
            # Full nose-down (-0.08) at low speed → neutral (0) at VR.
            vr_frac  = _clamp(ias_kts / max(self.vr, 1.0), 0.0, 1.0)
            elevator = -0.08 * (1.0 - vr_frac)

        elif self.phase == Phase.ROTATION:
            # Drive pitch_rate toward rotation_rate_dps
            target_rate = p.rotation_rate_dps
            if pitch >= p.rotation_pitch_deg:
                target_rate = 0.0   # hold once target reached
            rate_error = target_rate - pitch_rt
            elevator   = _clamp(rate_error * 0.15, -0.8, 0.8)

        elif self.phase == Phase.INITIAL_CLIMB:
            target_pitch = p.initial_climb_pitch
            elevator     = self._pitch_hold(pitch, pitch_rt, target_pitch)

        elif self.phase == Phase.CLIMB:
            # Speed-hold: adjust pitch to maintain climb_speed_kts
            speed_error  = ias_kts - p.climb_speed_kts
            target_pitch = p.climb_pitch - speed_error * self._KP_SPEED
            target_pitch = _clamp(target_pitch, 2.0, 20.0)
            elevator     = self._pitch_hold(pitch, pitch_rt, target_pitch)

        elif self.phase == Phase.LEVELOFF:
            elapsed      = t - self._phase_start_time
            target_pitch = max(0.0, p.climb_pitch - elapsed * 0.3)
            elevator     = self._pitch_hold(pitch, pitch_rt, target_pitch)

        elif self.phase == Phase.CRUISE:
            elevator = self._pitch_hold(pitch, pitch_rt, 0.0)

        return elevator, throttle

    def _pitch_hold(self, pitch, pitch_rt, target) -> float:
        """Simple PD pitch controller → elevator command."""
        err = target - pitch
        cmd = self._KP_PITCH * err - self._KD_PITCH * pitch_rt
        return _clamp(cmd, -1.0, 1.0)

    # ── Flap schedule ─────────────────────────────────────────────────────────
    def _manage_flaps(self, ias_kts: float):
        p = self.perf
        if self.phase == Phase.GROUND_ROLL:
            self.client.set_controls(flaps=p.flap_to_norm)
            return

        # Begin retraction at V2 + 10; fully retracted at V2 + 30
        if ias_kts >= self.v2 + 30:
            self.client.set_controls(flaps=0.0)
        elif ias_kts >= self.v2 + 10:
            frac = (ias_kts - (self.v2 + 10)) / 20.0
            new  = p.flap_to_norm * (1.0 - frac)
            self.client.set_controls(flaps=max(0.0, new))

    # ── Logging helpers ───────────────────────────────────────────────────────
    def _logged(self, label: str) -> bool:
        return any(e["label"] == label for e in self._log)

    def _log_event(self, label: str, t: float, speed: float, alt: float):
        entry = {"label": label, "sim_time_s": round(t, 1),
                 "speed_kts": round(speed, 1), "alt_ft": round(alt, 0)}
        self._log.append(entry)
        print(f"  [{t:7.1f}s] *** {label:18s}  spd={speed:6.1f} kts  alt={alt:7.0f} ft")

    # ── Console status ────────────────────────────────────────────────────────
    def _print_status(self, t, phase, gs, ias, alt, agl, vs, pitch, engines):
        def _f(v, fmt): return format(v, fmt) if v is not None else "n/a"
        eng = ""
        for i, e in enumerate(engines):
            rpm = e.get("rpm");  n1 = e.get("n1_pct")
            val = f"N1 {_f(n1, '.0f')}%" if n1 is not None else f"RPM {_f(rpm, '.0f')}"
            eng += f" ENG{i} {val}"
        print(
            f"  [{_f(t,'7.1f')}s] {phase.name:14s} "
            f"GS {_f(gs,'5.1f')} IAS {_f(ias,'5.1f')} kts | "
            f"ALT {_f(alt,'7.0f')} ft AGL {_f(agl,'6.0f')} ft | "
            f"VS {_f(vs,'+6.0f')} fpm  PITCH {_f(pitch,'+5.1f')}° |"
            f"{eng}"
        )
