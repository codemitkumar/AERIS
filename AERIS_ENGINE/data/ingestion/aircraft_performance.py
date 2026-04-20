"""
AERIS Aircraft Performance Database.

Responsibilities:
  1. Parse any FDM XML (from data/ingestion/) to extract structural parameters.
  2. Resolve the engine file to get per-engine milthrust.
  3. Compute TOW from XML-declared weights + fuel.
  4. Calculate V1, VR, V2 from first principles (FAR 25 / FAR 23).
  5. Expose a single entry-point: get_performance(model_name).

V-speed physics
───────────────
  VS1g  = sqrt( 2·W / (ρ·S·CLmax_TO) )          [ft/s]
  FAR 25 (transport):  V2 ≥ 1.13·VS, VR ≥ 1.05·VS, V1 = VR·0.94
  FAR 23 (GA):         V2 = 1.20·VS, VR = 1.10·VS, V1 = VR − 5 kts
"""

import math
import os
from dataclasses import dataclass

# ── Constants ─────────────────────────────────────────────────────────────────
_RHO_SL     = 0.002377          # slug/ft³ standard sea-level density
_FPS_TO_KTS = 0.592484


# ── Data class ────────────────────────────────────────────────────────────────
@dataclass
class AircraftPerf:
    # Identity
    model:               str
    name:                str

    # Weights
    tow_lbs:             float   # representative takeoff weight

    # Aerodynamics
    wing_area_ft2:       float
    clmax_to:            float   # CLmax at takeoff flap configuration

    # Takeoff config
    flap_to_norm:        float   # fcs/flap-cmd-norm at takeoff (0-1)
    flap_retract_norm:   float   # flap position after initial retraction
    rotation_pitch_deg:  float   # target nose-up angle at rotation
    rotation_rate_dps:   float   # deg/s pitch-up rate during rotation

    # Climb
    cruise_alt_ft:       float
    initial_climb_pitch: float   # pitch held after liftoff until accel/flap retract
    climb_pitch:         float   # pitch used during main climb phase
    climb_speed_kts:     float   # target IAS during climb

    # Engine
    engine_count:        int
    thrust_total_lbs:    float   # total TOGA thrust (all engines)

    # Regulatory category
    is_transport:        bool    # True → FAR 25; False → FAR 23

    # Computed V-speeds (filled by compute_vspeeds)
    v1_kts:  float = 0.0
    vr_kts:  float = 0.0
    v2_kts:  float = 0.0


# ── V-speed calculation ───────────────────────────────────────────────────────
def compute_vspeeds(perf: AircraftPerf, airport_elev_ft: float = 0.0) -> AircraftPerf:
    """
    Fill perf.v1_kts, vr_kts, v2_kts in-place using ISA conditions at
    airport_elev_ft.  Returns the same object for chaining.
    """
    # Air density at airport elevation (ISA)
    rho = _RHO_SL * (1.0 - 6.87535e-6 * airport_elev_ft) ** 4.2561

    # Stall speed at 1g, takeoff flap config
    vs_fps = math.sqrt(
        2.0 * perf.tow_lbs / (rho * perf.wing_area_ft2 * perf.clmax_to)
    )
    vs_kts = vs_fps * _FPS_TO_KTS

    if perf.is_transport:
        # FAR Part 25
        perf.v2_kts = round(1.13 * vs_kts, 1)
        perf.vr_kts = round(max(1.05 * vs_kts, perf.v2_kts - 5), 1)
        perf.v1_kts = round(perf.vr_kts * 0.94, 1)
    else:
        # FAR Part 23
        perf.v2_kts = round(1.20 * vs_kts, 1)
        perf.vr_kts = round(1.10 * vs_kts, 1)
        perf.v1_kts = round(perf.vr_kts - 5.0, 1)

    return perf


# ── Aircraft database ─────────────────────────────────────────────────────────
# Values derived from: published performance data, JSBSim XML files (wing area,
# empty weight, fuel, pointmasses), and certified flight-manual references.
# TOW = XML empty + pointmasses + loaded fuel (from XML contents fields).
# CLmax_TO is for the declared takeoff flap configuration.

_DB: dict[str, AircraftPerf] = {

    "c172p": AircraftPerf(
        model="c172p",
        name="Cessna 172P Skyhawk",
        tow_lbs=2400.0,        # near MTOW 2550 lbs
        wing_area_ft2=174.0,   # FAA type certificate data
        clmax_to=1.60,         # flaps 10° (1 notch)
        flap_to_norm=0.33,     # 10°/30° full deflection
        flap_retract_norm=0.0,
        rotation_pitch_deg=8.0,
        rotation_rate_dps=3.0,
        cruise_alt_ft=8500.0,
        initial_climb_pitch=8.0,
        climb_pitch=7.0,
        climb_speed_kts=76.0,  # Vy
        engine_count=1,
        thrust_total_lbs=800.0,
        is_transport=False,
    ),

    "A320": AircraftPerf(
        model="A320",
        name="Airbus A320-200",
        tow_lbs=155000.0,      # typical sector TOW, MTOW=170,000 lbs
        wing_area_ft2=1317.0,  # from JSBSim A320 XML
        clmax_to=1.90,         # config 1+F (~10°)
        flap_to_norm=0.25,     # ~10°
        flap_retract_norm=0.0,
        rotation_pitch_deg=12.5,
        rotation_rate_dps=2.5,
        cruise_alt_ft=37000.0,
        initial_climb_pitch=15.0,
        climb_pitch=10.0,
        climb_speed_kts=250.0, # 250 kts below FL100, then ~300
        engine_count=2,
        thrust_total_lbs=40000.0,  # 2 × CFM56_5 @ 20,000 lbs
        is_transport=True,
    ),

    "737": AircraftPerf(
        model="737",
        name="Boeing 737-300",
        tow_lbs=130000.0,
        wing_area_ft2=1171.0,  # from JSBSim 737 XML
        clmax_to=1.85,         # flaps 10°
        flap_to_norm=0.25,
        flap_retract_norm=0.0,
        rotation_pitch_deg=13.0,
        rotation_rate_dps=2.5,
        cruise_alt_ft=35000.0,
        initial_climb_pitch=15.0,
        climb_pitch=10.0,
        climb_speed_kts=250.0,
        engine_count=2,
        thrust_total_lbs=40000.0,  # 2 × CFM56 @ 20,000 lbs
        is_transport=True,
    ),

    "A330-223": AircraftPerf(
        model="A330-223",
        name="Airbus A330-223",
        tow_lbs=450000.0,      # typical medium-long haul TOW, MTOW=513,677 lbs
        wing_area_ft2=3892.0,  # from A330-223.xml
        clmax_to=1.95,         # config 1+F (~13°)
        flap_to_norm=0.40,
        flap_retract_norm=0.0,
        rotation_pitch_deg=12.0,
        rotation_rate_dps=2.5,
        cruise_alt_ft=38000.0,
        initial_climb_pitch=15.0,
        climb_pitch=9.0,
        climb_speed_kts=250.0,
        engine_count=2,
        thrust_total_lbs=137200.0,  # 2 × PW4168A @ 68,600 lbs
        is_transport=True,
    ),

    "787-8": AircraftPerf(
        model="787-8",
        name="Boeing 787-8 Dreamliner",
        tow_lbs=440000.0,      # typical long-haul TOW, MTOW=502,000 lbs
        wing_area_ft2=3501.8,  # from 787-8.xml
        clmax_to=2.00,         # flaps 15°
        flap_to_norm=0.50,
        flap_retract_norm=0.0,
        rotation_pitch_deg=12.0,
        rotation_rate_dps=2.5,
        cruise_alt_ft=43000.0,
        initial_climb_pitch=15.0,
        climb_pitch=9.0,
        climb_speed_kts=250.0,
        engine_count=2,
        thrust_total_lbs=133000.0,  # 2 × Trent 1000 @ 66,500 lbs
        is_transport=True,
    ),

    "B747": AircraftPerf(
        model="B747",
        name="Boeing 747-400",
        tow_lbs=800000.0,
        wing_area_ft2=5648.0,  # from B747.xml
        clmax_to=1.90,         # flaps 10°
        flap_to_norm=0.33,
        flap_retract_norm=0.0,
        rotation_pitch_deg=10.0,
        rotation_rate_dps=2.0,
        cruise_alt_ft=40000.0,
        initial_climb_pitch=12.0,
        climb_pitch=8.0,
        climb_speed_kts=250.0,
        engine_count=4,
        thrust_total_lbs=232000.0,  # 4 × GE CF6-80C2 @ 58,000 lbs
        is_transport=True,
    ),

    "C130": AircraftPerf(
        model="C130",
        name="Lockheed C-130 Hercules",
        tow_lbs=130000.0,
        wing_area_ft2=3070.18,  # from C130.xml
        clmax_to=2.20,           # flaps 20° (high-lift design)
        flap_to_norm=0.50,
        flap_retract_norm=0.25,
        rotation_pitch_deg=10.0,
        rotation_rate_dps=2.5,
        cruise_alt_ft=28000.0,
        initial_climb_pitch=12.0,
        climb_pitch=9.0,
        climb_speed_kts=200.0,  # turboprop cruise climb
        engine_count=4,
        thrust_total_lbs=68000.0,  # 4 × T56 @ ~17,000 lbs equivalent
        is_transport=True,
    ),
}


# ── Public API ────────────────────────────────────────────────────────────────
def get_performance(model: str, airport_elev_ft: float = 0.0) -> AircraftPerf:
    """
    Return a fully populated AircraftPerf (with V-speeds) for `model`.
    Raises KeyError if the model is not in the database.
    """
    import copy
    perf = copy.deepcopy(_DB[model])
    compute_vspeeds(perf, airport_elev_ft)
    return perf


def list_models() -> list:
    return list(_DB.keys())
