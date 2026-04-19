import asyncio
import json
import random
from enum import Enum, auto


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ─────────────────────────────────────────────
# Flight Phases
# ─────────────────────────────────────────────
class Phase(Enum):
    GROUND_ROLL = auto()
    ROTATION = auto()
    ROTATION_HOLD = auto()   # ← NEW
    INITIAL_CLIMB = auto()
    CLIMB = auto()
    CRUISE_HOLD = auto()
    COMPLETE = auto()


# ─────────────────────────────────────────────
# Takeoff Manager
# ─────────────────────────────────────────────
class TakeoffManager:

    _KP_PITCH = 0.04
    _KD_PITCH = 0.04

    def __init__(self, client, perf, log_file="flight_data.jsonl", ws=None, dt=1/30):
        self.client = client
        self.perf = perf
        self.ws = ws
        self.dt = dt

        self.phase = Phase.GROUND_ROLL
        self._phase_start_time = 0.0

        self.v1 = perf.v1_kts
        self.vr = perf.vr_kts
        self.v2 = perf.v2_kts

        self._vr_seen_time = 0.0

        self.cruise_levels = [10000, 20000, 30000]
        self.current_level_index = 0

        self.log_fp = open(log_file, "w")

        self._prev_elevator = 0.0

    # ─────────────────────────────────────────────
    # Sensor simulation
    # ─────────────────────────────────────────────
    def _noise(self, v, n):
        return v + random.uniform(-n, n)

    def _dual(self, v, n):
        return self._noise(v, n), self._noise(v, n)

    def _disagree(self, a, b, th):
        return abs(a - b) > th

    # ─────────────────────────────────────────────
    # Stability
    # ─────────────────────────────────────────────
    def _stability(self, roll, roll_rate, yaw_rate):
        # High gain so saturation kicks in at any roll > 2° — prevents buildup
        aileron = _clamp(-0.30 * roll - 0.10 * roll_rate, -0.7, 0.7)
        rudder  = _clamp(-0.20 * yaw_rate, -0.6, 0.6)
        return aileron, rudder

    # ─────────────────────────────────────────────
    # Pitch hold (stable PID)
    # ─────────────────────────────────────────────
    def _pitch_hold(self, pitch, pitch_rt, target):
        err = target - pitch
        pitch_rt = _clamp(pitch_rt, -5, 5)

        cmd = self._KP_PITCH * err - self._KD_PITCH * pitch_rt
        cmd = _clamp(cmd, -0.3, 0.3)

        cmd = _clamp(cmd,
                     self._prev_elevator - 0.05,
                     self._prev_elevator + 0.05)

        self._prev_elevator = cmd
        return cmd

    # ─────────────────────────────────────────────
    # Control law
    # ─────────────────────────────────────────────
    def _control(self, t, ias, pitch, pitch_rt, vs, roll, roll_rate, yaw_rate):

        throttle = 1.0

        # ─── Elevator ─────────────────────────────
        if self.phase == Phase.GROUND_ROLL:
            elevator = -0.05

        elif self.phase == Phase.ROTATION:
            # Rate control until 8°, then hold 8° — avoids overshooting into a stall
            if pitch < 8:
                err = 2.0 - pitch_rt   # target 2 deg/s nose-up rate
                elevator = _clamp(0.08 * err, -0.3, 0.3)
            else:
                elevator = self._pitch_hold(pitch, pitch_rt, 8)

        elif self.phase == Phase.ROTATION_HOLD:
            elevator = self._pitch_hold(pitch, pitch_rt, 10)

        else:
            # ─── Speed-based pitch control ───
            target_speed = self.v2 + 10
            speed_error = target_speed - ias

            # deadzone
            if abs(speed_error) < 3:
                speed_error = 0

            target_pitch = 10 + 0.02 * speed_error

            # VS damping (softer)
            vs_error = 1500 - vs
            target_pitch += 0.0008 * vs_error

            target_pitch = _clamp(target_pitch, 5, 15)

            elevator = self._pitch_hold(pitch, pitch_rt, target_pitch)

        # ─── Stability ────────────────────────────
        aileron, rudder = self._stability(roll, roll_rate, yaw_rate)

        # Hard safety override — fires early before the roll can build to unrecoverable
        if abs(roll) > 20:
            aileron = -0.7 if roll > 0 else 0.7

        return elevator, throttle, aileron, rudder

    # ─────────────────────────────────────────────
    # Phase transitions
    # ─────────────────────────────────────────────
    def _update_phase(self, t, ias, agl, vs, wow):

        if self.phase == Phase.GROUND_ROLL:
            if ias >= self.vr:
                if self._vr_seen_time == 0:
                    self._vr_seen_time = t
                elif t - self._vr_seen_time > 0.5:
                    self.phase = Phase.ROTATION
                    self._phase_start_time = t
            else:
                self._vr_seen_time = 0

        elif self.phase == Phase.ROTATION:
            if not wow and vs > 50:
                self.phase = Phase.ROTATION_HOLD
                self._phase_start_time = t

        elif self.phase == Phase.ROTATION_HOLD:
            if t - self._phase_start_time > 2.5:
                self.phase = Phase.INITIAL_CLIMB
                self._phase_start_time = t

        elif self.phase == Phase.INITIAL_CLIMB:
            if agl > 500:
                self.phase = Phase.CLIMB

        elif self.phase == Phase.CLIMB:
            target_alt = self.cruise_levels[self.current_level_index]
            if agl >= target_alt - 200:
                self.phase = Phase.CRUISE_HOLD
                self._phase_start_time = t

        elif self.phase == Phase.CRUISE_HOLD:
            if t - self._phase_start_time > 300:
                self.current_level_index += 1
                if self.current_level_index >= len(self.cruise_levels):
                    self.phase = Phase.COMPLETE
                else:
                    self.phase = Phase.CLIMB
                    self._phase_start_time = t

    # ─────────────────────────────────────────────
    # Logging
    # ─────────────────────────────────────────────
    def _log(self, data):
        self.log_fp.write(json.dumps(data) + "\n")
        self.log_fp.flush()

    # ─────────────────────────────────────────────
    # Main loop
    # ─────────────────────────────────────────────
    async def run(self):

        self.client.set_controls(throttle=1.0)

        while self.phase != Phase.COMPLETE:

            self.client.step()
            state = self.client.get_state()

            jsb = state.get("jsbsim", {})
            der = state.get("derived", {})
            meta = state.get("meta", {})

            t = meta.get("sim_time_s", 0.0)

            ias = jsb.get("velocities/vc-kts", 0.0)
            agl = jsb.get("position/h-agl-ft", 0.0)
            vs = der.get("vertical_speed_fpm", 0.0)

            pitch = der.get("pitch_deg", 0.0)
            pitch_rt = der.get("pitch_rate_dps", 0.0)
            roll = der.get("roll_deg", 0.0)
            yaw_rate = der.get("yaw_rate_dps", 0.0)
            roll_rate = der.get("roll_rate_dps", 0.0)

            heading = jsb.get("attitude/psi-deg", 0.0)
            gs = jsb.get("velocities/vg-kts", ias)

            wow = jsb.get("gear/unit[0]/WOW", 0)

            # Phase update
            self._update_phase(t, ias, agl, vs, wow)

            # Control
            elevator, throttle, aileron, rudder = self._control(
                t, ias, pitch, pitch_rt, vs, roll, roll_rate, yaw_rate
            )

            self.client.set_controls(
                throttle=throttle,
                elevator=elevator,
                aileron=aileron,
                rudder=rudder
            )

            # ───────── SENSOR LAYER ─────────
            pitch_c, pitch_f = self._dual(pitch, 0.2)
            roll_c, roll_f = self._dual(roll, 0.2)
            hdg_c, hdg_f = self._dual(heading, 0.5)

            ias_c, ias_f = self._dual(ias, 1.0)
            alt_c, alt_f = self._dual(agl, 5)
            vs_c, vs_f = self._dual(vs, 50)

            pitch_dis = self._disagree(pitch_c, pitch_f, 3)
            roll_dis = self._disagree(roll_c, roll_f, 5)
            alt_dis = self._disagree(alt_c, alt_f, 100)
            ias_dis = self._disagree(ias_c, ias_f, 10)

            any_dis = pitch_dis or roll_dis or alt_dis or ias_dis

            captain_suspect = ias_dis or pitch_dis
            fo_suspect = False
            both_suspect = captain_suspect and fo_suspect

            recommended_action = ""
            if both_suspect:
                recommended_action = "UNRELIABLE AIRSPEED — USE PITCH/POWER"
            elif captain_suspect:
                recommended_action = "Cross-check FO instruments"

            clean = {
                "time": t,
                "phase": self.phase.name,
                "groundspeed_kts": gs,

                "pitch_captain": pitch_c,
                "roll_captain": roll_c,
                "heading_captain": hdg_c,
                "ias_captain": ias_c,
                "alt_captain": alt_c,
                "vs_captain": vs_c,

                "pitch_fo": pitch_f,
                "roll_fo": roll_f,
                "heading_fo": hdg_f,
                "ias_fo": ias_f,
                "alt_fo": alt_f,
                "vs_fo": vs_f,

                "any_disagree": any_dis,
                "pitch_disagree": pitch_dis,
                "roll_disagree": roll_dis,
                "alt_disagree": alt_dis,

                "captain_suspect": captain_suspect,
                "fo_suspect": fo_suspect,
                "both_suspect": both_suspect,
                "recommended_action": recommended_action,

                "v1_kts": self.v1,
                "vr_kts": self.vr,
                "v2_kts": self.v2,
            }

            self._log(clean)

            if self.ws:
                await self.ws.broadcast(clean)

            await asyncio.sleep(self.dt)

        self.log_fp.close()
        print("✅ Simulation complete")