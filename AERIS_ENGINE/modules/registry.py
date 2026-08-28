from core.data_bus import DataBus
from modules.assessment.aircraft_health                 import AircraftHealthModule
from decision.decision_engine                            import DiversionDecisionModule
from modules.math.altimeter_setting                     import AltimeterSettingModule
from modules.math.unreliable_speed                      import UnreliableSpeedModule
from modules.math.altimeter_cross_check                 import AltimeterCrossCheckModule
from modules.math.uncommanded_descent                   import UncommandedDescentModule
from modules.math.rapid_altitude_loss                   import RapidAltitudeLossModule
from modules.math.energy_state                          import EnergyStateModule
from modules.math.structural_altitude_rate              import StructuralAltitudeRateModule
from modules.math.overspeed                             import OverspeedModule
from modules.math.fuel_leak_detection                   import FuelLeakDetectionModule
from modules.math.fuel_imbalance                        import FuelImbalanceModule

# Speed modules (SPD-3 to SPD-7)
from modules.math.speed.stall_warning                   import StallWarningModule
from modules.math.speed.low_speed_alert                 import LowSpeedAlertModule
from modules.math.speed.flap_overspeed                  import FlapOverspeedModule
from modules.math.speed.gear_overspeed                  import GearOverspeedModule
from modules.math.speed.maneuvering_speed               import ManeuveringSpeedModule

# Glide modules (GLI-1 to GLI-5)
from modules.math.glide.glide_range                     import GlideRangeModule
from modules.math.glide.best_glide_speed                import BestGlideSpeedModule
from modules.math.glide.emergency_descent               import EmergencyDescentModule
from modules.math.glide.glideslope_deviation            import GlideslopeDeviationModule
from modules.math.glide.vref_monitor                    import VrefMonitorModule

# Engine modules (ENG-1 to ENG-6)
from modules.math.engine.n1_disagree                    import N1DisagreeModule
from modules.math.engine.egt_overtemp                   import EGTOvertempModule
from modules.math.engine.thrust_asymmetry               import ThrustAsymmetryModule
from modules.math.engine.engine_failure                 import EngineFailureModule
from modules.math.engine.efficiency_trend               import EfficiencyTrendModule
from modules.math.engine.compressor_stall               import CompressorStallModule

# Attitude modules (ATT-1 to ATT-5)
from modules.math.attitude.unusual_attitude             import UnusualAttitudeModule
from modules.math.attitude.bank_angle                   import BankAngleModule
from modules.math.attitude.pitch_limit                  import PitchLimitModule
from modules.math.attitude.sustained_g                  import SustainedGModule
from modules.math.attitude.sideslip                     import SideslipModule

# Pressurization modules (PRESS-1 to PRESS-3)
from modules.math.pressurization.cabin_altitude         import CabinAltitudeModule
from modules.math.pressurization.rapid_decompression    import RapidDecompressionModule
from modules.math.pressurization.diff_pressure          import DiffPressureModule

# GPWS modules (GPWS-1 to GPWS-5)
from modules.math.gpws.excessive_sink_rate              import ExcessiveSinkRateModule
from modules.math.gpws.terrain_closure                  import TerrainClosureModule
from modules.math.gpws.altitude_loss_takeoff            import AltitudeLossTakeoffModule
from modules.math.gpws.unsafe_config                    import UnsafeConfigModule
from modules.math.gpws.glideslope_below                 import GlideslopeBelowModule

# Approach modules (APPR-1 to APPR-5)
from modules.math.approach.unstable_approach            import UnstableApproachModule
from modules.math.approach.low_energy                   import LowEnergyModule
from modules.math.approach.crosswind_limit              import CrosswindLimitModule
from modules.math.approach.tailwind                     import TailwindModule
from modules.math.approach.go_around                    import GoAroundAdvisoryModule

# Icing modules (ICE-1 to ICE-3)
from modules.math.icing.icing_conditions                import IcingConditionsModule
from modules.math.icing.anti_ice_off                    import AntiIceOffModule
from modules.math.icing.ice_accumulation                import IceAccumulationModule

# Fuel prediction modules (FUEL-3 to FUEL-6)
from modules.math.fuel.fuel_exhaustion                  import FuelExhaustionModule
from modules.math.fuel.min_diversion_fuel               import MinDiversionFuelModule
from modules.math.fuel.fuel_efficiency                  import FuelEfficiencyModule
from modules.math.fuel.endurance_calculator             import EnduranceCalculatorModule

# Performance modules (PERF-1 to PERF-3)
from modules.math.performance.cruise_altitude           import CruiseAltitudeModule
from modules.math.performance.density_altitude          import DensityAltitudeModule
from modules.math.performance.takeoff_performance       import TakeoffPerformanceModule


def register_all(bus: DataBus, ws=None, perf=None) -> None:
    """Register every active module with the data bus.

    Each module subscribes once here and receives every flight-state tick
    automatically without touching main.py or the flight loop.

    Parameters
    ----------
    perf : AircraftPerf | None
        Pass the aircraft performance object so modules that need
        aircraft-specific limits (e.g. VMO/MMO) can read them at
        construction time.
    """
    # ── Altitude / Altimetry ──────────────────────────────────────────────────
    AltimeterSettingModule(ws=ws).attach(bus)                # ALT-6: QNH / baro calibration

    # ── Speed ─────────────────────────────────────────────────────────────────
    UnreliableSpeedModule(ws=ws).attach(bus)
    OverspeedModule(ws=ws, perf=perf).attach(bus)            # SPD-2: VMO/MMO / VNE
    StallWarningModule(ws=ws, perf=perf).attach(bus)         # SPD-3: stall warning
    LowSpeedAlertModule(ws=ws, perf=perf).attach(bus)        # SPD-4: low speed on approach
    FlapOverspeedModule(ws=ws, perf=perf).attach(bus)        # SPD-5: flap overspeed
    GearOverspeedModule(ws=ws, perf=perf).attach(bus)        # SPD-6: gear overspeed
    ManeuveringSpeedModule(ws=ws, perf=perf).attach(bus)     # SPD-7: Va manoeuvring

    # ── Altitude ──────────────────────────────────────────────────────────────
    AltimeterCrossCheckModule(ws=ws).attach(bus)             # ALT-1: PF/PM cross-check
    UncommandedDescentModule(ws=ws).attach(bus)              # ALT-2: descent without command
    RapidAltitudeLossModule(ws=ws).attach(bus)               # ALT-3: windshear / rapid loss
    EnergyStateModule(ws=ws).attach(bus)                     # ALT-4: total energy protection
    StructuralAltitudeRateModule(ws=ws).attach(bus)          # ALT-5: G-limit / altitude jerk

    # ── Glide ─────────────────────────────────────────────────────────────────
    GlideRangeModule(ws=ws, perf=perf).attach(bus)           # GLI-1: engine-out range
    BestGlideSpeedModule(ws=ws, perf=perf).attach(bus)       # GLI-2: best glide speed
    EmergencyDescentModule(ws=ws).attach(bus)                # GLI-3: emergency descent rate
    GlideslopeDeviationModule(ws=ws).attach(bus)             # GLI-4: 3° glideslope deviation
    VrefMonitorModule(ws=ws).attach(bus)                     # GLI-5: VREF approach speed

    # ── Engine ────────────────────────────────────────────────────────────────
    N1DisagreeModule(ws=ws).attach(bus)                      # ENG-1: N1 spread
    EGTOvertempModule(ws=ws, perf=perf).attach(bus)          # ENG-2: EGT overtemp
    ThrustAsymmetryModule(ws=ws, perf=perf).attach(bus)      # ENG-3: thrust asymmetry
    EngineFailureModule(ws=ws).attach(bus)                   # ENG-4: engine failure
    EfficiencyTrendModule(ws=ws).attach(bus)                 # ENG-5: efficiency trend
    CompressorStallModule(ws=ws).attach(bus)                 # ENG-6: compressor stall

    # ── Attitude ──────────────────────────────────────────────────────────────
    UnusualAttitudeModule(ws=ws).attach(bus)                 # ATT-1: unusual attitude
    BankAngleModule(ws=ws).attach(bus)                       # ATT-2: excessive bank
    PitchLimitModule(ws=ws).attach(bus)                      # ATT-3: pitch limit
    SustainedGModule(ws=ws).attach(bus)                      # ATT-4: sustained G
    SideslipModule(ws=ws).attach(bus)                        # ATT-5: sideslip

    # ── Pressurization ────────────────────────────────────────────────────────
    CabinAltitudeModule(ws=ws).attach(bus)                   # PRESS-1: cabin altitude
    RapidDecompressionModule(ws=ws).attach(bus)              # PRESS-2: rapid decompression
    DiffPressureModule(ws=ws).attach(bus)                    # PRESS-3: differential pressure

    # ── GPWS ──────────────────────────────────────────────────────────────────
    ExcessiveSinkRateModule(ws=ws).attach(bus)               # GPWS-1: sink rate (Mode 1)
    TerrainClosureModule(ws=ws).attach(bus)                  # GPWS-2: terrain closure (Mode 2)
    AltitudeLossTakeoffModule(ws=ws).attach(bus)             # GPWS-3: alt loss takeoff (Mode 3)
    UnsafeConfigModule(ws=ws).attach(bus)                    # GPWS-4: unsafe config (Mode 4)
    GlideslopeBelowModule(ws=ws).attach(bus)                 # GPWS-5: glideslope below (Mode 5)

    # ── Approach ──────────────────────────────────────────────────────────────
    UnstableApproachModule(ws=ws, perf=perf).attach(bus)     # APPR-1: unstable approach
    LowEnergyModule(ws=ws).attach(bus)                       # APPR-2: low energy state
    CrosswindLimitModule(ws=ws, perf=perf).attach(bus)       # APPR-3: crosswind limit
    TailwindModule(ws=ws).attach(bus)                        # APPR-4: tailwind limit
    GoAroundAdvisoryModule(ws=ws, perf=perf).attach(bus)     # APPR-5: go-around advisory

    # ── Icing ─────────────────────────────────────────────────────────────────
    IcingConditionsModule(ws=ws).attach(bus)                 # ICE-1: icing envelope
    AntiIceOffModule(ws=ws).attach(bus)                      # ICE-2: anti-ice off in icing
    IceAccumulationModule(ws=ws).attach(bus)                 # ICE-3: ice buildup estimator

    # ── Fuel ──────────────────────────────────────────────────────────────────
    FuelLeakDetectionModule(ws=ws).attach(bus)               # FUEL-1: unaccounted tank loss
    FuelImbalanceModule(ws=ws).attach(bus)                   # FUEL-2: L/R tank imbalance
    FuelExhaustionModule(ws=ws).attach(bus)                  # FUEL-3: time/dist to exhaustion
    MinDiversionFuelModule(ws=ws).attach(bus)                # FUEL-4: minimum diversion fuel
    FuelEfficiencyModule(ws=ws).attach(bus)                  # FUEL-5: specific range monitor
    EnduranceCalculatorModule(ws=ws).attach(bus)             # FUEL-6: endurance hours

    # ── Performance ───────────────────────────────────────────────────────────
    CruiseAltitudeModule(ws=ws, perf=perf).attach(bus)       # PERF-1: cruise altitude check
    DensityAltitudeModule(ws=ws).attach(bus)                 # PERF-2: density altitude
    TakeoffPerformanceModule(ws=ws, perf=perf).attach(bus)   # PERF-3: takeoff acceleration

    # ── Assessment (registered LAST — reads tracker after all modules ran) ────
    AircraftHealthModule(ws).attach(bus)                     # Layer 2: subsystem health

    # ── Decision (registered after Assessment — reads state["assessment"]) ────
    DiversionDecisionModule(perf=perf, tracker=ws).attach(bus)  # Layer 3: diversion airports
