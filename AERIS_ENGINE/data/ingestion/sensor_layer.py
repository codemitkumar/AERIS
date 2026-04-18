"""
AERIS Sensor Layer

Sits between JSBSim physics (truth values) and the FlightDataAdapter.

Real aircraft architecture modelled
────────────────────────────────────
  Captain side  — ADIRU 1 + Captain's ADC/AHRS instruments
  F/O side      — ADIRU 2 + F/O's ADC/AHRS instruments

JSBSim provides ONE physics truth. This layer produces two independent
sensor channels — each with its own noise, calibration bias, and injectable
failure modes — to mimic what actual onboard LRUs would report.

Sensors modelled per channel
─────────────────────────────
  ADC  (Air Data Computer)        IAS, TAS, Mach, Altitude, VS
  AHRS (Attitude/Heading Ref.)    Pitch, Roll, Heading

Navigation data (Lat, Lon, Groundspeed, Track) comes from GPS/IRS and is
treated as a single shared source for now.

ADC Failure modes
─────────────────
  PITOT_BLOCK        Tube physically blocked.
                     IAS/TAS/Mach freeze then decay toward 0 over ~2 min.

  PITOT_ICING        AF447-style icing (4-phase progression):
                     Phase 1 (0-10 s):  erratic ±15 kt fluctuations.
                     Phase 2 (10-35 s): rapid drop toward 0.
                     Phase 3 (35-65 s): near-zero, erratic.
                     Phase 4 (65 s+):   recovery ∝ (1 - severity).

  STATIC_PORT_BLOCK  Static port blocked.
                     Altitude freezes, VS → 0, IAS overreads above freeze alt.

  ADC_FAILURE        Complete ADC/ADIRU failure — all outputs zeroed, invalid.

AHRS Failure modes
──────────────────
  AHRS_FAILURE       Complete AHRS/IRS failure — pitch/roll/heading zeroed,
                     valid flag → False.
"""

import math
import random
from enum import Enum
from dataclasses import dataclass
from typing import Optional


# ── Failure types ──────────────────────────────────────────────────────────────
class FailureType(str, Enum):
    NONE              = "none"
    PITOT_BLOCK       = "pitot_block"
    PITOT_ICING       = "pitot_icing"
    STATIC_PORT_BLOCK = "static_port_block"
    ADC_FAILURE       = "adc_failure"
    AHRS_FAILURE      = "ahrs_failure"


# ── Noise / bias constants ─────────────────────────────────────────────────────
_IAS_NOISE_KTS      = 0.3     # 1-σ Gaussian noise
_ALT_NOISE_FT       = 5.0
_VS_NOISE_FPM       = 20.0
_PITCH_NOISE_DEG    = 0.10
_ROLL_NOISE_DEG     = 0.10
_HEADING_NOISE_DEG  = 0.20

_CAL_BIAS_KTS       = 0.4     # fixed per-channel calibration offset (±)
_CAL_BIAS_ALT_FT    = 8.0
_CAL_BIAS_PITCH_DEG = 0.15
_CAL_BIAS_ROLL_DEG  = 0.15

# Cross-check disagree thresholds (A320/A330 EFCS)
_IAS_DISAGREE_KTS   = 5.0
_ALT_DISAGREE_FT    = 200.0
_PITCH_DISAGREE_DEG = 2.0
_ROLL_DISAGREE_DEG  = 3.0


# ── Internal failure state ─────────────────────────────────────────────────────
@dataclass
class _FailureState:
    ftype:      FailureType = FailureType.NONE
    severity:   float       = 1.0
    start_t:    float       = 0.0
    frozen_ias: float       = 0.0
    frozen_tas: float       = 0.0
    frozen_alt: float       = 0.0
    frozen_mach: float      = 0.0
    active:     bool        = False


# ── Sensor channel output ──────────────────────────────────────────────────────
@dataclass
class SensorChannel:
    name: str
    # ADC outputs
    ias_kts:            float
    tas_kts:            float
    mach:               float
    altitude_ft:        float
    vs_fpm:             float
    adc_valid:          bool
    adc_failure_type:   str
    adc_failure_active: bool
    # AHRS outputs
    pitch_deg:          float
    roll_deg:           float
    heading_deg:        float
    ahrs_valid:         bool
    ahrs_failure_active: bool


# ── Main class ─────────────────────────────────────────────────────────────────
class SensorLayer:
    """
    Parameters
    ----------
    seed : int, optional
        RNG seed for reproducible noise (useful for training data generation).
        None → different noise each run.
    """

    def __init__(self, seed: Optional[int] = None):
        self._rng = random.Random(seed)

        # Per-side fixed calibration biases (set once at init)
        self._bias = {
            "captain": {
                "ias":     self._rng.uniform(-_CAL_BIAS_KTS,       _CAL_BIAS_KTS),
                "alt":     self._rng.uniform(-_CAL_BIAS_ALT_FT,    _CAL_BIAS_ALT_FT),
                "pitch":   self._rng.uniform(-_CAL_BIAS_PITCH_DEG, _CAL_BIAS_PITCH_DEG),
                "roll":    self._rng.uniform(-_CAL_BIAS_ROLL_DEG,  _CAL_BIAS_ROLL_DEG),
            },
            "fo": {
                "ias":     self._rng.uniform(-_CAL_BIAS_KTS,       _CAL_BIAS_KTS),
                "alt":     self._rng.uniform(-_CAL_BIAS_ALT_FT,    _CAL_BIAS_ALT_FT),
                "pitch":   self._rng.uniform(-_CAL_BIAS_PITCH_DEG, _CAL_BIAS_PITCH_DEG),
                "roll":    self._rng.uniform(-_CAL_BIAS_ROLL_DEG,  _CAL_BIAS_ROLL_DEG),
            },
        }

        # Independent failure states for ADC and AHRS per side
        self._adc_failures: dict[str, _FailureState] = {
            "captain": _FailureState(),
            "fo":      _FailureState(),
        }
        self._ahrs_failures: dict[str, _FailureState] = {
            "captain": _FailureState(),
            "fo":      _FailureState(),
        }

    # ── Failure injection API ──────────────────────────────────────────────────
    def inject_failure(
        self,
        side:         str,
        failure_type: FailureType,
        severity:     float = 1.0,
        sim_time:     float = 0.0,
        current_ias:  float = 0.0,
        current_tas:  float = 0.0,
        current_alt:  float = 0.0,
        current_mach: float = 0.0,
    ):
        """
        Inject a failure into one sensor channel.

        AHRS_FAILURE routes to the AHRS failure state; all other types
        route to the ADC failure state.

        Example — AF447-style pitot icing on both sides:
            sensor.inject_failure("captain", FailureType.PITOT_ICING,
                                  severity=0.9, sim_time=t, ...)
            sensor.inject_failure("fo",      FailureType.PITOT_ICING,
                                  severity=0.85, sim_time=t, ...)
        """
        if failure_type == FailureType.AHRS_FAILURE:
            fs = self._ahrs_failures[side]
            fs.ftype    = failure_type
            fs.severity = severity
            fs.start_t  = sim_time
            fs.active   = True
        else:
            fs            = self._adc_failures[side]
            fs.ftype      = failure_type
            fs.severity   = severity
            fs.start_t    = sim_time
            fs.frozen_ias  = current_ias
            fs.frozen_tas  = current_tas
            fs.frozen_alt  = current_alt
            fs.frozen_mach = current_mach
            fs.active      = True

        print(f"[SensorLayer] {side.upper()} — {failure_type.value} injected "
              f"(severity={severity:.2f}, t={sim_time:.1f}s)")

    def clear_failure(self, side: str):
        """Remove all active failures from one channel."""
        self._adc_failures[side]  = _FailureState()
        self._ahrs_failures[side] = _FailureState()
        print(f"[SensorLayer] {side.upper()} — all failures cleared")

    def clear_adc_failure(self, side: str):
        self._adc_failures[side] = _FailureState()

    def clear_ahrs_failure(self, side: str):
        self._ahrs_failures[side] = _FailureState()

    # ── Main processing step ───────────────────────────────────────────────────
    def process(self, jsbsim_state: dict, sim_time: float = 0.0) -> dict:
        """
        Call every simulation frame with the raw JSBSim state dict.

        Returns:
          "captain"    → SensorChannel dict (ADC + AHRS)
          "fo"         → SensorChannel dict (ADC + AHRS)
          "cross_check"→ delta comparisons + suspect flags
          "navigation" → shared GPS/IRS navigation data
        """
        jsb = jsbsim_state.get("jsbsim",  {})
        der = jsbsim_state.get("derived", {})

        # Physics truth from JSBSim
        truth_ias     = jsb.get("velocities/vc-kts")    or 0.0
        truth_tas     = jsb.get("velocities/vtrue-kts") or 0.0
        truth_mach    = jsb.get("velocities/mach")      or 0.0
        truth_alt     = jsb.get("position/h-sl-ft")     or 0.0
        truth_vs      = der.get("vertical_speed_fpm")   or 0.0
        truth_gs      = (jsb.get("velocities/vg-fps") or 0.0) * 0.592484
        truth_pitch   = der.get("pitch_deg")             or 0.0
        truth_roll    = der.get("roll_deg")              or 0.0
        truth_heading = der.get("heading_deg")           or 0.0
        truth_lat     = jsb.get("position/lat-gc-deg")  or 0.0
        truth_lon     = jsb.get("position/long-gc-deg") or 0.0

        cap = self._compute_channel(
            "captain", truth_ias, truth_tas, truth_mach, truth_alt, truth_vs,
            truth_pitch, truth_roll, truth_heading, sim_time,
        )
        fo = self._compute_channel(
            "fo", truth_ias, truth_tas, truth_mach, truth_alt, truth_vs,
            truth_pitch, truth_roll, truth_heading, sim_time,
        )

        xcheck = self._cross_check(cap, fo, truth_gs, truth_ias)

        navigation = {
            "groundspeed_kts": round(truth_gs, 1),
            "latitude_deg":    round(truth_lat, 6),
            "longitude_deg":   round(truth_lon, 6),
            "track_deg":       round(truth_heading, 1),
        }

        return {
            "captain":     _ch_to_dict(cap),
            "fo":          _ch_to_dict(fo),
            "cross_check": xcheck,
            "navigation":  navigation,
        }

    # ── Per-channel computation ────────────────────────────────────────────────
    def _compute_channel(
        self, side: str,
        truth_ias, truth_tas, truth_mach, truth_alt, truth_vs,
        truth_pitch, truth_roll, truth_heading,
        t: float,
    ) -> SensorChannel:

        adc_fs  = self._adc_failures[side]
        ahrs_fs = self._ahrs_failures[side]
        bias    = self._bias[side]
        rng     = self._rng

        # ── ADC computation ────────────────────────────────────────────
        if not adc_fs.active:
            ias  = truth_ias  + bias["ias"] + rng.gauss(0, _IAS_NOISE_KTS)
            tas  = truth_tas  + rng.gauss(0, _IAS_NOISE_KTS)
            mach = truth_mach + rng.gauss(0, 0.001)
            alt  = truth_alt  + bias["alt"] + rng.gauss(0, _ALT_NOISE_FT)
            vs   = truth_vs   + rng.gauss(0, _VS_NOISE_FPM)
            adc_valid = True

        else:
            elapsed  = max(0.0, t - adc_fs.start_t)
            severity = adc_fs.severity

            if adc_fs.ftype == FailureType.PITOT_BLOCK:
                decay = math.exp(-elapsed / (120.0 / severity))
                ias   = adc_fs.frozen_ias  * decay + rng.gauss(0, 0.2)
                tas   = adc_fs.frozen_tas  * decay + rng.gauss(0, 0.2)
                mach  = adc_fs.frozen_mach * decay
                alt   = truth_alt + bias["alt"] + rng.gauss(0, _ALT_NOISE_FT)
                vs    = truth_vs  + rng.gauss(0, _VS_NOISE_FPM)

            elif adc_fs.ftype == FailureType.PITOT_ICING:
                if elapsed < 10.0:
                    noise = rng.gauss(0, 15.0 * severity * (elapsed / 10.0))
                    ias   = truth_ias  + noise
                    tas   = truth_tas  + noise
                    mach  = truth_mach
                elif elapsed < 35.0:
                    frac  = (elapsed - 10.0) / 25.0
                    ias   = adc_fs.frozen_ias  * (1.0 - frac * severity) + rng.gauss(0, 3.0)
                    tas   = adc_fs.frozen_tas  * (1.0 - frac * severity)
                    mach  = adc_fs.frozen_mach * (1.0 - frac * severity)
                elif elapsed < 65.0:
                    base  = adc_fs.frozen_ias * max(0.0, 1.0 - severity)
                    ias   = base + rng.gauss(0, 8.0 * severity)
                    tas   = base + rng.gauss(0, 5.0)
                    mach  = 0.05 * rng.random() if severity > 0.7 else truth_mach * 0.2
                else:
                    recover = min(1.0, (elapsed - 65.0) / 30.0) * (1.0 - severity)
                    ias   = truth_ias  * recover + rng.gauss(0, 2.0)
                    tas   = truth_tas  * recover
                    mach  = truth_mach * recover

                ias  = max(0.0, ias)
                tas  = max(0.0, tas)
                mach = max(0.0, mach)
                alt  = truth_alt + bias["alt"] + rng.gauss(0, _ALT_NOISE_FT)
                vs   = truth_vs  + rng.gauss(0, _VS_NOISE_FPM)

            elif adc_fs.ftype == FailureType.STATIC_PORT_BLOCK:
                delta_alt  = truth_alt - adc_fs.frozen_alt
                ias_factor = 1.0 + delta_alt * 1.5e-4
                ias  = truth_ias * ias_factor + rng.gauss(0, _IAS_NOISE_KTS)
                tas  = truth_tas + rng.gauss(0, _IAS_NOISE_KTS)
                mach = truth_mach
                alt  = adc_fs.frozen_alt + rng.gauss(0, 2.0)
                vs   = rng.gauss(0, 5.0)

            elif adc_fs.ftype == FailureType.ADC_FAILURE:
                p, r, h = self._ahrs_pitch(side, truth_pitch, truth_roll, truth_heading,
                                           ahrs_fs, bias, rng)
                return SensorChannel(
                    name=side,
                    ias_kts=0.0, tas_kts=0.0, mach=0.0, altitude_ft=0.0, vs_fpm=0.0,
                    adc_valid=False, adc_failure_type=adc_fs.ftype.value, adc_failure_active=True,
                    pitch_deg=p, roll_deg=r, heading_deg=h,
                    ahrs_valid=not ahrs_fs.active,
                    ahrs_failure_active=ahrs_fs.active,
                )

            else:
                ias  = truth_ias;  tas  = truth_tas;  mach = truth_mach
                alt  = truth_alt;  vs   = truth_vs

            adc_valid = True

        # ── AHRS computation ───────────────────────────────────────────
        pitch, roll, heading, ahrs_valid = self._compute_ahrs(
            truth_pitch, truth_roll, truth_heading, ahrs_fs, bias, rng
        )

        return SensorChannel(
            name=side,
            ias_kts=round(ias,  1),
            tas_kts=round(tas,  1),
            mach=round(mach, 4),
            altitude_ft=round(alt, 0),
            vs_fpm=round(vs, 0),
            adc_valid=adc_valid,
            adc_failure_type=adc_fs.ftype.value,
            adc_failure_active=adc_fs.active,
            pitch_deg=round(pitch, 2),
            roll_deg=round(roll, 2),
            heading_deg=round(heading, 1),
            ahrs_valid=ahrs_valid,
            ahrs_failure_active=ahrs_fs.active,
        )

    def _compute_ahrs(self, truth_pitch, truth_roll, truth_heading,
                      ahrs_fs: _FailureState, bias: dict, rng) -> tuple:
        if not ahrs_fs.active:
            pitch   = truth_pitch   + bias["pitch"] + rng.gauss(0, _PITCH_NOISE_DEG)
            roll    = truth_roll    + bias["roll"]  + rng.gauss(0, _ROLL_NOISE_DEG)
            heading = (truth_heading + rng.gauss(0, _HEADING_NOISE_DEG)) % 360.0
            return pitch, roll, heading, True
        # AHRS_FAILURE: all outputs zeroed
        return 0.0, 0.0, 0.0, False

    def _ahrs_pitch(self, _side, truth_pitch, truth_roll, truth_heading, ahrs_fs, bias, rng):
        """Helper to compute rounded AHRS tuple for early-return cases."""
        p, r, h, _ = self._compute_ahrs(truth_pitch, truth_roll, truth_heading, ahrs_fs, bias, rng)
        return round(p, 2), round(r, 2), round(h, 1)

    # ── Cross-check logic ──────────────────────────────────────────────────────
    def _cross_check(
        self,
        cap: SensorChannel,
        fo:  SensorChannel,
        groundspeed_kts: float,
        truth_ias: float,
    ) -> dict:

        ias_delta     = abs(cap.ias_kts      - fo.ias_kts)
        alt_delta     = abs(cap.altitude_ft  - fo.altitude_ft)
        tas_delta     = abs(cap.tas_kts      - fo.tas_kts)
        mach_delta    = abs(cap.mach         - fo.mach)
        pitch_delta   = abs(cap.pitch_deg    - fo.pitch_deg)
        roll_delta    = abs(cap.roll_deg     - fo.roll_deg)
        heading_delta = abs(cap.heading_deg  - fo.heading_deg)
        # wrap heading delta to [0, 180]
        if heading_delta > 180:
            heading_delta = 360 - heading_delta

        avg_ias      = (cap.ias_kts + fo.ias_kts) / 2.0
        ias_gs_delta = abs(avg_ias - groundspeed_kts)

        def _is_suspect(ch: SensorChannel, other: SensorChannel) -> bool:
            if not ch.adc_valid:
                return True
            if ias_delta > _IAS_DISAGREE_KTS:
                my_gs_err    = abs(ch.ias_kts    - groundspeed_kts)
                other_gs_err = abs(other.ias_kts - groundspeed_kts)
                if my_gs_err > other_gs_err + 10:
                    return True
            if ch.ias_kts < 60 and other.ias_kts > 100:
                return True
            if ch.ias_kts > 500:
                return True
            return False

        cap_suspect  = _is_suspect(cap, fo)
        fo_suspect   = _is_suspect(fo,  cap)
        both_suspect = (
            ias_delta > _IAS_DISAGREE_KTS and cap_suspect and fo_suspect
        ) or (
            avg_ias < 60 and groundspeed_kts > 100
        )

        if not cap_suspect and not fo_suspect:
            action = "NORMAL"
        elif both_suspect:
            action = "UNRELIABLE_AIRSPEED_BOTH"
        elif cap_suspect:
            action = "CHECK_CAPTAIN"
        else:
            action = "CHECK_FO"

        return {
            "ias_delta_kts":      round(ias_delta,     1),
            "alt_delta_ft":       round(alt_delta,      0),
            "tas_delta_kts":      round(tas_delta,      1),
            "mach_delta":         round(mach_delta,     4),
            "pitch_delta_deg":    round(pitch_delta,    2),
            "roll_delta_deg":     round(roll_delta,     2),
            "heading_delta_deg":  round(heading_delta,  1),
            "groundspeed_kts":    round(groundspeed_kts, 1),
            "avg_ias_kts":        round(avg_ias,         1),
            "ias_vs_groundspeed": round(ias_gs_delta,    1),
            "any_disagree":       ias_delta > _IAS_DISAGREE_KTS,
            "alt_disagree":       alt_delta > _ALT_DISAGREE_FT,
            "pitch_disagree":     pitch_delta > _PITCH_DISAGREE_DEG,
            "roll_disagree":      roll_delta  > _ROLL_DISAGREE_DEG,
            "captain_suspect":    cap_suspect,
            "fo_suspect":         fo_suspect,
            "both_suspect":       both_suspect,
            "recommended_action": action,
        }


# ── Dict serialisation helper ──────────────────────────────────────────────────
def _ch_to_dict(ch: SensorChannel) -> dict:
    return {
        "ias_kts":             ch.ias_kts,
        "tas_kts":             ch.tas_kts,
        "mach":                ch.mach,
        "altitude_ft":         ch.altitude_ft,
        "vs_fpm":              ch.vs_fpm,
        "adc_valid":           ch.adc_valid,
        "adc_failure_type":    ch.adc_failure_type,
        "adc_failure_active":  ch.adc_failure_active,
        "pitch_deg":           ch.pitch_deg,
        "roll_deg":            ch.roll_deg,
        "heading_deg":         ch.heading_deg,
        "ahrs_valid":          ch.ahrs_valid,
        "ahrs_failure_active": ch.ahrs_failure_active,
    }
