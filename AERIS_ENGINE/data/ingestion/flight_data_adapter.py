"""
AERIS Flight Data Adapter

Sits between the Sensor Layer and all downstream consumers (math modules,
AI modules, WebSocket broadcasts).

Architecture
────────────
  JSBSim  →  SensorLayer  →  FlightDataAdapter  →  Math / AI modules
                                                 →  WebSocket clients

The adapter merges captain and F/O sensor channels plus navigation data
into a single flat dict.  All downstream code reads ONLY from this dict —
it never touches JSBSim or SensorLayer directly.

Why this layer exists
─────────────────────
  In simulation:  JSBSim + SensorLayer supply the data.
  On a real aircraft:  remove JSBSim + SensorLayer, plug in the actual
  flight computer data bus, and implement get_data() against that source.
  The math and AI modules remain unchanged.
"""

from data.ingestion.sensor_layer import SensorLayer


class FlightDataAdapter:
    """
    Parameters
    ----------
    sensor_layer : SensorLayer
        Initialised sensor layer wrapping a live JSBSim client.
    """

    def __init__(self, sensor_layer: SensorLayer):
        self._sensor = sensor_layer
        self._last: dict = {}

    def get_data(self, jsbsim_state: dict, sim_time: float = 0.0) -> dict:
        """
        Process one simulation frame and return the unified flight data dict.

        All keys use the convention  <parameter>_captain / <parameter>_fo
        for dual-channel values.  Shared values (navigation, cross-checks)
        have no suffix.

        Returns
        -------
        dict with keys:

          ADC — Captain
            ias_captain, tas_captain, mach_captain,
            alt_captain, vs_captain,
            adc_captain_valid, adc_captain_failure

          ADC — F/O
            ias_fo, tas_fo, mach_fo,
            alt_fo, vs_fo,
            adc_fo_valid, adc_fo_failure

          AHRS — Captain
            pitch_captain, roll_captain, heading_captain, ahrs_captain_valid

          AHRS — F/O
            pitch_fo, roll_fo, heading_fo, ahrs_fo_valid

          Navigation (shared GPS/IRS)
            groundspeed_kts, latitude_deg, longitude_deg, track_deg

          Cross-check deltas
            ias_delta_kts, alt_delta_ft, tas_delta_kts, mach_delta,
            pitch_delta_deg, roll_delta_deg, heading_delta_deg,
            avg_ias_kts, ias_vs_groundspeed

          Disagree flags
            any_disagree, alt_disagree, pitch_disagree, roll_disagree

          Suspect flags & guidance
            captain_suspect, fo_suspect, both_suspect, recommended_action

          Meta
            sim_time
        """
        raw = self._sensor.process(jsbsim_state, sim_time)
        cap = raw["captain"]
        fo  = raw["fo"]
        xc  = raw["cross_check"]
        nav = raw["navigation"]

        data = {
            # ── ADC Captain ───────────────────────────────────────────
            "ias_captain":          cap["ias_kts"],
            "tas_captain":          cap["tas_kts"],
            "mach_captain":         cap["mach"],
            "alt_captain":          cap["altitude_ft"],
            "vs_captain":           cap["vs_fpm"],
            "adc_captain_valid":    cap["adc_valid"],
            "adc_captain_failure":  cap["adc_failure_type"],

            # ── ADC F/O ───────────────────────────────────────────────
            "ias_fo":               fo["ias_kts"],
            "tas_fo":               fo["tas_kts"],
            "mach_fo":              fo["mach"],
            "alt_fo":               fo["altitude_ft"],
            "vs_fo":                fo["vs_fpm"],
            "adc_fo_valid":         fo["adc_valid"],
            "adc_fo_failure":       fo["adc_failure_type"],

            # ── AHRS Captain ──────────────────────────────────────────
            "pitch_captain":        cap["pitch_deg"],
            "roll_captain":         cap["roll_deg"],
            "heading_captain":      cap["heading_deg"],
            "ahrs_captain_valid":   cap["ahrs_valid"],

            # ── AHRS F/O ──────────────────────────────────────────────
            "pitch_fo":             fo["pitch_deg"],
            "roll_fo":              fo["roll_deg"],
            "heading_fo":           fo["heading_deg"],
            "ahrs_fo_valid":        fo["ahrs_valid"],

            # ── Navigation ────────────────────────────────────────────
            "groundspeed_kts":      nav["groundspeed_kts"],
            "latitude_deg":         nav["latitude_deg"],
            "longitude_deg":        nav["longitude_deg"],
            "track_deg":            nav["track_deg"],

            # ── Cross-check deltas ────────────────────────────────────
            "ias_delta_kts":        xc["ias_delta_kts"],
            "alt_delta_ft":         xc["alt_delta_ft"],
            "tas_delta_kts":        xc["tas_delta_kts"],
            "mach_delta":           xc["mach_delta"],
            "pitch_delta_deg":      xc["pitch_delta_deg"],
            "roll_delta_deg":       xc["roll_delta_deg"],
            "heading_delta_deg":    xc["heading_delta_deg"],
            "avg_ias_kts":          xc["avg_ias_kts"],
            "ias_vs_groundspeed":   xc["ias_vs_groundspeed"],

            # ── Disagree flags ────────────────────────────────────────
            "any_disagree":         xc["any_disagree"],
            "alt_disagree":         xc["alt_disagree"],
            "pitch_disagree":       xc["pitch_disagree"],
            "roll_disagree":        xc["roll_disagree"],

            # ── Suspect flags & guidance ──────────────────────────────
            "captain_suspect":      xc["captain_suspect"],
            "fo_suspect":           xc["fo_suspect"],
            "both_suspect":         xc["both_suspect"],
            "recommended_action":   xc["recommended_action"],

            # ── Meta ──────────────────────────────────────────────────
            "sim_time":             sim_time,
        }

        self._last = data
        return data

    @property
    def last(self) -> dict:
        """Returns the most recent get_data() result without reprocessing."""
        return self._last
