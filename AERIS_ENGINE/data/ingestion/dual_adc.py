"""
AERIS Dual ADC (Air Data Computer) Simulation Layer.

Real A320/A330 air data architecture
─────────────────────────────────────
  ADIRU 1  →  Captain's PFD   (IAS, ALT, VS, TAS, Mach)
  ADIRU 2  →  F/O's PFD
  ADIRU 3  →  Standby / backup
  ISIS     →  Independent standby instrument

JSBSim provides ONE physics truth.  This module sits on top and produces
two independent ADC channels — each with its own sensor noise, calibration
offset, and injectable failure modes.

Failure modes modelled
──────────────────────
  PITOT_BLOCK        Tube physically blocked (water, insect, maintenance cap).
                     IAS freezes then decays toward 0.  TAS and Mach follow.
                     Static-side unaffected → altitude and VS remain valid.

  PITOT_ICING        Ice builds on tube (classic rain/cloud icing at altitude).
                     Phase 1 (0-10 s): erratic fluctuations ± 15 kts.
                     Phase 2 (10-30 s): rapid drop toward 0 as tube ices over.
                     Phase 3 (30-60 s): partial recovery / further erratic.
                     Phase 4 (60 s+): full recovery if severity < 1.0.
                     AF447 pattern: all tubes ice → all speeds unreliable.

  STATIC_PORT_BLOCK  Static port blocked.  Static pressure freezes at the
                     altitude of blockage.
                     - Altitude FREEZES (static ≡ constant).
                     - VS drops to 0.
                     - IAS OVERREADS as aircraft climbs above frozen alt
                       (lower actual static → higher differential pressure).

  ADC_FAILURE        Complete ADC/ADIRU failure.
                     All outputs → 0, valid flag → False.

Cross-check outputs (what the AI model trains on)
──────────────────────────────────────────────────
  ias_delta_kts          |captain_IAS − fo_IAS|
  alt_delta_ft           |captain_ALT − fo_ALT|
  tas_delta_kts          |captain_TAS − fo_TAS|
  mach_delta             |captain_Mach − fo_Mach|
  ias_vs_groundspeed     how much IAS (avg) diverges from GPS groundspeed
                         (after rough wind correction)
  any_disagree           IAS delta > 5 kts  (EFCS threshold for A320/A330)
  captain_suspect        heuristic: captain channel looks bad
  fo_suspect             heuristic: F/O channel looks bad
  both_suspect           both channels unreliable → hardest scenario for crew
  recommended_action     string: "NORMAL" | "CHECK_CAPTAIN" | "CHECK_FO"
                                 | "UNRELIABLE_AIRSPEED_BOTH"
"""

import math
import random
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional


# ── Failure types ─────────────────────────────────────────────────────────────
class FailureType(str, Enum):
    NONE               = "none"
    PITOT_BLOCK        = "pitot_block"
    PITOT_ICING        = "pitot_icing"
    STATIC_PORT_BLOCK  = "static_port_block"
    ADC_FAILURE        = "adc_failure"


# ── Normal sensor noise (independent per channel) ─────────────────────────────
_IAS_NOISE_KTS    = 0.3    # 1-sigma Gaussian
_ALT_NOISE_FT     = 5.0
_VS_NOISE_FPM     = 20.0
_CAL_BIAS_KTS     = 0.4    # fixed per-channel calibration offset (±0.4 kts)
_CAL_BIAS_ALT_FT  = 8.0

# Disagree thresholds (A320/A330 EFCS)
_IAS_DISAGREE_KTS = 5.0
_ALT_DISAGREE_FT  = 200.0


# ── Failure state per side ────────────────────────────────────────────────────
@dataclass
class _FailureState:
    ftype:     FailureType = FailureType.NONE
    severity:  float       = 1.0     # 0.0 = mild, 1.0 = full
    start_t:   float       = 0.0     # sim time when failure injected
    frozen_ias: float      = 0.0
    frozen_tas: float      = 0.0
    frozen_alt: float      = 0.0
    frozen_mach: float     = 0.0
    active:    bool        = False


# ── ADC Channel output ────────────────────────────────────────────────────────
@dataclass
class ADCChannel:
    name:              str
    ias_kts:           float
    tas_kts:           float
    mach:              float
    altitude_ft:       float
    vs_fpm:            float
    valid:             bool
    failure_type:      str
    failure_active:    bool


# ── Main class ────────────────────────────────────────────────────────────────
class DualADC:
    """
    Parameters
    ----------
    seed : int, optional
        RNG seed for reproducible noise (useful for training data generation).
        None → different noise each run (realistic for flight sim).
    """

    def __init__(self, seed: Optional[int] = None):
        self._rng = random.Random(seed)

        # Per-side calibration bias (fixed offsets, set once)
        self._bias = {
            "captain": {
                "ias": self._rng.uniform(-_CAL_BIAS_KTS, _CAL_BIAS_KTS),
                "alt": self._rng.uniform(-_CAL_BIAS_ALT_FT, _CAL_BIAS_ALT_FT),
            },
            "fo": {
                "ias": self._rng.uniform(-_CAL_BIAS_KTS, _CAL_BIAS_KTS),
                "alt": self._rng.uniform(-_CAL_BIAS_ALT_FT, _CAL_BIAS_ALT_FT),
            },
        }

        self._failures: dict[str, _FailureState] = {
            "captain": _FailureState(),
            "fo":      _FailureState(),
        }

    # ── Failure injection API ─────────────────────────────────────────────────
    def inject_failure(
        self,
        side:         str,           # "captain" | "fo"
        failure_type: FailureType,
        severity:     float = 1.0,
        sim_time:     float = 0.0,
        current_ias:  float = 0.0,
        current_tas:  float = 0.0,
        current_alt:  float = 0.0,
        current_mach: float = 0.0,
    ):
        """
        Inject a failure into one ADC channel.

        Example — inject AF447-style pitot icing on both sides:
            dual_adc.inject_failure("captain", FailureType.PITOT_ICING,
                                    severity=0.9, sim_time=t, ...)
            dual_adc.inject_failure("fo",      FailureType.PITOT_ICING,
                                    severity=0.85, sim_time=t, ...)
        """
        fs = self._failures[side]
        fs.ftype      = failure_type
        fs.severity   = severity
        fs.start_t    = sim_time
        fs.frozen_ias = current_ias
        fs.frozen_tas = current_tas
        fs.frozen_alt = current_alt
        fs.frozen_mach = current_mach
        fs.active     = True
        print(f"[DualADC] {side.upper()} — {failure_type.value} injected "
              f"(severity={severity:.2f}, IAS={current_ias:.1f} kts at t={sim_time:.1f}s)")

    def clear_failure(self, side: str):
        """Remove injected failure from one channel."""
        self._failures[side] = _FailureState()
        print(f"[DualADC] {side.upper()} — failure cleared")

    # ── Main processing step ──────────────────────────────────────────────────
    def process(self, jsbsim_state: dict, sim_time: float = 0.0) -> dict:
        """
        Call every simulation frame with the raw JSBSim state dict.

        Returns a dict with keys:
          "captain"     → ADC channel dict
          "fo"          → ADC channel dict
          "cross_check" → comparison + suspect flags
        """
        jsb = jsbsim_state.get("jsbsim", {})
        der = jsbsim_state.get("derived", {})

        # Truth values from JSBSim physics
        truth_ias  = jsb.get("velocities/vc-kts")   or 0.0
        truth_tas  = jsb.get("velocities/vtrue-kts") or 0.0
        truth_mach = jsb.get("velocities/mach")      or 0.0
        truth_alt  = jsb.get("position/h-sl-ft")     or 0.0
        truth_vs   = (der.get("vertical_speed_fpm")  or 0.0)
        truth_gs   = (jsb.get("velocities/vg-fps") or 0.0) * 0.592484

        # Compute each channel
        cap = self._compute_channel("captain", truth_ias, truth_tas, truth_mach,
                                    truth_alt, truth_vs, sim_time)
        fo  = self._compute_channel("fo",      truth_ias, truth_tas, truth_mach,
                                    truth_alt, truth_vs, sim_time)

        # Cross-check
        xcheck = self._cross_check(cap, fo, truth_gs, truth_ias)

        return {
            "captain":     _ch_to_dict(cap),
            "fo":          _ch_to_dict(fo),
            "cross_check": xcheck,
        }

    # ── Per-channel computation ───────────────────────────────────────────────
    def _compute_channel(
        self, side: str,
        truth_ias, truth_tas, truth_mach, truth_alt, truth_vs,
        t: float,
    ) -> ADCChannel:

        fs   = self._failures[side]
        bias = self._bias[side]
        rng  = self._rng

        if not fs.active:
            # ── Normal operation ──────────────────────────────────────
            ias  = truth_ias  + bias["ias"] + rng.gauss(0, _IAS_NOISE_KTS)
            tas  = truth_tas  + rng.gauss(0, _IAS_NOISE_KTS)
            mach = truth_mach + rng.gauss(0, 0.001)
            alt  = truth_alt  + bias["alt"] + rng.gauss(0, _ALT_NOISE_FT)
            vs   = truth_vs   + rng.gauss(0, _VS_NOISE_FPM)
            return ADCChannel(side, round(ias,1), round(tas,1), round(mach,4),
                              round(alt,0), round(vs,0), True, "none", False)

        elapsed  = max(0.0, t - fs.start_t)
        severity = fs.severity

        # ── PITOT_BLOCK ───────────────────────────────────────────────
        if fs.ftype == FailureType.PITOT_BLOCK:
            # IAS freezes immediately, then decays to 0 over ~2 minutes
            decay   = math.exp(-elapsed / (120.0 / severity))
            ias     = fs.frozen_ias * decay + rng.gauss(0, 0.2)
            tas     = fs.frozen_tas * decay + rng.gauss(0, 0.2)
            mach    = fs.frozen_mach * decay
            alt     = truth_alt + bias["alt"] + rng.gauss(0, _ALT_NOISE_FT)
            vs      = truth_vs  + rng.gauss(0, _VS_NOISE_FPM)

        # ── PITOT_ICING ───────────────────────────────────────────────
        elif fs.ftype == FailureType.PITOT_ICING:
            if elapsed < 10.0:
                # Phase 1: erratic ± 15 kts fluctuations
                noise  = rng.gauss(0, 15.0 * severity * (elapsed / 10.0))
                ias    = truth_ias + noise
                tas    = truth_tas + noise
                mach   = truth_mach
            elif elapsed < 35.0:
                # Phase 2: rapid drop toward 0
                frac   = (elapsed - 10.0) / 25.0
                ias    = fs.frozen_ias * (1.0 - frac * severity) + rng.gauss(0, 3.0)
                tas    = fs.frozen_tas * (1.0 - frac * severity)
                mach   = fs.frozen_mach * (1.0 - frac * severity)
            elif elapsed < 65.0:
                # Phase 3: near-zero but erratic
                base   = fs.frozen_ias * max(0.0, 1.0 - severity)
                ias    = base + rng.gauss(0, 8.0 * severity)
                tas    = base + rng.gauss(0, 5.0)
                mach   = 0.05 * rng.random() if severity > 0.7 else truth_mach * 0.2
            else:
                # Phase 4: recovery proportional to (1 - severity)
                recover = min(1.0, (elapsed - 65.0) / 30.0) * (1.0 - severity)
                ias    = truth_ias * recover + rng.gauss(0, 2.0)
                tas    = truth_tas * recover
                mach   = truth_mach * recover

            ias    = max(0.0, ias)
            tas    = max(0.0, tas)
            mach   = max(0.0, mach)
            alt    = truth_alt + bias["alt"] + rng.gauss(0, _ALT_NOISE_FT)
            vs     = truth_vs  + rng.gauss(0, _VS_NOISE_FPM)

        # ── STATIC_PORT_BLOCK ─────────────────────────────────────────
        elif fs.ftype == FailureType.STATIC_PORT_BLOCK:
            # Static pressure frozen → altitude frozen, VS → 0
            # IAS OVERREADS as actual static drops below frozen static
            # Δalt = actual_alt - frozen_alt (how much higher we are)
            delta_alt  = truth_alt - fs.frozen_alt
            # ISA: ΔP/P ≈ -delta_alt * 3.566e-5   →   affects IAS
            ias_factor = 1.0 + delta_alt * 1.5e-4    # ~1.5% per 1000 ft above freeze
            ias  = truth_ias * ias_factor + rng.gauss(0, _IAS_NOISE_KTS)
            tas  = truth_tas + rng.gauss(0, _IAS_NOISE_KTS)  # TAS from IRS unaffected
            mach = truth_mach  # Mach from IRS
            alt  = fs.frozen_alt + rng.gauss(0, 2.0)   # altitude frozen
            vs   = rng.gauss(0, 5.0)                    # VS ≈ 0

        # ── ADC_FAILURE ───────────────────────────────────────────────
        elif fs.ftype == FailureType.ADC_FAILURE:
            ias  = 0.0;  tas  = 0.0;  mach = 0.0
            alt  = 0.0;  vs   = 0.0
            return ADCChannel(side, 0.0, 0.0, 0.0, 0.0, 0.0,
                              False, fs.ftype.value, True)

        else:
            ias  = truth_ias;  tas = truth_tas;  mach = truth_mach
            alt  = truth_alt;  vs  = truth_vs

        return ADCChannel(
            name=side,
            ias_kts=round(ias, 1),
            tas_kts=round(tas, 1),
            mach=round(mach, 4),
            altitude_ft=round(alt, 0),
            vs_fpm=round(vs, 0),
            valid=True,
            failure_type=fs.ftype.value,
            failure_active=fs.active,
        )

    # ── Cross-check logic ─────────────────────────────────────────────────────
    def _cross_check(
        self,
        cap: ADCChannel,
        fo:  ADCChannel,
        groundspeed_kts: float,
        truth_ias: float,
    ) -> dict:

        ias_delta  = abs(cap.ias_kts  - fo.ias_kts)
        alt_delta  = abs(cap.altitude_ft - fo.altitude_ft)
        tas_delta  = abs(cap.tas_kts  - fo.tas_kts)
        mach_delta = abs(cap.mach     - fo.mach)

        # Average of both channels (what crew might mentally use)
        avg_ias    = (cap.ias_kts + fo.ias_kts) / 2.0

        # IAS vs GPS groundspeed — large delta = suspect
        # (wind correction rough estimate: ±50 kts error tolerance)
        ias_gs_delta = abs(avg_ias - groundspeed_kts)

        # Suspect heuristics
        #  - Speed dropped >30 kts from a believable value
        #  - Speed is implausibly low given other channel and GPS
        #  - Channel is invalid

        def _is_suspect(ch: ADCChannel, other: ADCChannel) -> bool:
            if not ch.valid:
                return True
            # Large disagree with other side
            if ias_delta > _IAS_DISAGREE_KTS:
                # Which side is further from GPS?
                my_gs_err    = abs(ch.ias_kts    - groundspeed_kts)
                other_gs_err = abs(other.ias_kts - groundspeed_kts)
                if my_gs_err > other_gs_err + 10:
                    return True
            # Implausibly low speed (< 60 kts) when other channel is normal
            if ch.ias_kts < 60 and other.ias_kts > 100:
                return True
            # Implausible high speed
            if ch.ias_kts > 500:
                return True
            return False

        cap_suspect = _is_suspect(cap, fo)
        fo_suspect  = _is_suspect(fo, cap)
        both_suspect = (
            ias_delta > _IAS_DISAGREE_KTS and cap_suspect and fo_suspect
        ) or (
            avg_ias < 60 and groundspeed_kts > 100  # both low, GPS says fast
        )

        # Recommended action
        if not cap_suspect and not fo_suspect:
            action = "NORMAL"
        elif both_suspect:
            action = "UNRELIABLE_AIRSPEED_BOTH"
        elif cap_suspect:
            action = "CHECK_CAPTAIN"
        else:
            action = "CHECK_FO"

        return {
            # Deltas
            "ias_delta_kts":        round(ias_delta,  1),
            "alt_delta_ft":         round(alt_delta,  0),
            "tas_delta_kts":        round(tas_delta,  1),
            "mach_delta":           round(mach_delta, 4),
            # GPS cross-check
            "groundspeed_kts":      round(groundspeed_kts, 1),
            "avg_ias_kts":          round(avg_ias, 1),
            "ias_vs_groundspeed":   round(ias_gs_delta, 1),
            # Flags
            "any_disagree":         ias_delta > _IAS_DISAGREE_KTS,
            "alt_disagree":         alt_delta > _ALT_DISAGREE_FT,
            "captain_suspect":      cap_suspect,
            "fo_suspect":           fo_suspect,
            "both_suspect":         both_suspect,
            # Guidance
            "recommended_action":   action,
        }


# ── Dict serialisation helper ─────────────────────────────────────────────────
def _ch_to_dict(ch: ADCChannel) -> dict:
    return {
        "ias_kts":        ch.ias_kts,
        "tas_kts":        ch.tas_kts,
        "mach":           ch.mach,
        "altitude_ft":    ch.altitude_ft,
        "vs_fpm":         ch.vs_fpm,
        "valid":          ch.valid,
        "failure_type":   ch.failure_type,
        "failure_active": ch.failure_active,
    }
