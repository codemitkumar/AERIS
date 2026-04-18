"""
AERIS JSBSim ingestion layer.

Core principle: zero hardcoded property lists.
At init, the property catalog for the loaded model is queried and every
readable property under the flight-relevant namespaces is discovered
automatically. get_state() reads all of them every frame.

This means the same code works identically for c172p, A320, 737, A330, etc.
The AI model downstream receives everything JSBSim computes — nothing less.
"""

import jsbsim
import math
import os

# ── Directory constants ───────────────────────────────────────────────────────
_HERE    = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(os.path.dirname(_HERE))   # AERIS_ENGINE root

# ── Unit helpers ─────────────────────────────────────────────────────────────
_FPS_TO_KTS = 0.592484
_R_TO_C     = lambda r: (r - 491.67) * 5.0 / 9.0    # Rankine → Celsius
_PSF_TO_HPA = 0.478803                                # lb/ft²  → hPa
_RHO_SL     = 0.002377                                # slug/ft³ standard sea-level

# ── Namespaces to capture from the JSBSim property catalog ───────────────────
_CAPTURE_PREFIXES = (
    "position/",
    "velocities/",
    "attitude/",
    "aero/",
    "accelerations/",
    "propulsion/",
    "fcs/",
    "gear/",
    "forces/",
    "moments/",
    "atmosphere/",
    "metrics/",
    "inertia/",
    "simulation/",
)

# ── Properties to probe when detecting how many engines exist ─────────────────
_ENGINE_PROBE = (
    "engine-rpm",           # piston
    "n1",                   # turbine
    "n2",                   # turbine
    "thrust-lbs",           # both
    "fuel-flow-rate-pps",   # both
)

# ── Default initial conditions ────────────────────────────────────────────────
# In-flight defaults (used when no ic= is supplied).
# For takeoff, pass a complete ground-start ic dict via the constructor.
_DEFAULT_IC = {
    "ic/lat-gc-deg":   28.56,
    "ic/long-gc-deg":  77.10,
    "ic/h-sl-ft":      3000.0,
    "ic/u-fps":        200.0,    # ~118 kts; override for jets
    "ic/v-fps":        0.0,
    "ic/w-fps":        0.0,
    "ic/phi-deg":      0.0,
    "ic/theta-deg":    2.0,
    "ic/psi-true-deg": 90.0,
}

# Ground-start IC — use as ic= argument for takeoff simulation
GROUND_START_IC = {
    "ic/lat-gc-deg":   28.5665,  # VIDP (Indira Gandhi Intl)
    "ic/long-gc-deg":  77.1031,
    "ic/h-sl-ft":      0.0,      # sea-level runway; JSBSim places on ground
    "ic/vt-kts":       0.0,      # standing start
    "ic/u-fps":        0.0,
    "ic/v-fps":        0.0,
    "ic/w-fps":        0.0,
    "ic/phi-deg":      0.0,
    "ic/theta-deg":    0.0,      # nose level
    "ic/psi-true-deg": 100.0,    # VIDP Runway 10
}


class JSBSimClient:
    """
    Parameters
    ----------
    model            : JSBSim model name, e.g. "c172p", "A320-200", "737"
    ic               : dict of JSBSim IC properties to override defaults
    fuel_per_tank_lbs: float (all tanks) or list[float] (per tank). None = half capacity.
    max_engines      : upper bound for engine slot probing
    max_tanks        : upper bound for tank slot probing
    default_controls : initial control positions dict
    fg_xml           : path to flightgear.xml (default: ROOT_DIR/flightgear.xml)
    """

    def __init__(
        self,
        model:              str  = "c172p",
        ic:                 dict = None,
        fuel_per_tank_lbs        = None,
        max_engines:        int  = 4,
        max_tanks:          int  = 8,
        default_controls:   dict = None,
        fg_xml:             str  = None,
    ):
        self.model = model

        # ── JSBSim setup ──────────────────────────────────────────────
        self.fdm = jsbsim.FGFDMExec(None)
        self.fdm.set_debug_level(0)
        self.fdm.load_model(model)

        xml_path = fg_xml or os.path.join(ROOT_DIR, "flightgear.xml")
        self.fdm.set_output_directive(xml_path)
        self.fdm.set_dt(1.0 / 60.0)

        # ── Initial conditions ────────────────────────────────────────
        merged_ic = {**_DEFAULT_IC, **(ic or {})}
        for prop, val in merged_ic.items():
            try:
                self.fdm[prop] = val
            except Exception:
                pass

        # ── Fuel tanks ────────────────────────────────────────────────
        self._tank_count = self._detect_slots(
            "propulsion/tank[{i}]/contents-lbs", max_tanks)

        for i in range(self._tank_count):
            if isinstance(fuel_per_tank_lbs, list):
                lbs = fuel_per_tank_lbs[i] if i < len(fuel_per_tank_lbs) else 0.0
            elif fuel_per_tank_lbs is not None:
                lbs = float(fuel_per_tank_lbs)
            else:
                cap = self._g(f"propulsion/tank[{i}]/capacity-lbs") or 200.0
                lbs = cap * 0.5
            try:
                self.fdm[f"propulsion/tank[{i}]/contents-lbs"] = lbs
            except Exception:
                pass

        # ── Start engines & run IC ────────────────────────────────────
        try:
            self.fdm["propulsion/set-running"] = -1
        except Exception:
            pass
        self.fdm.run_ic()

        # ── Discover engines ──────────────────────────────────────────
        self._engine_count = self._detect_engines(max_engines)

        # ── Discover ALL readable properties for this model ───────────
        self._all_props = self._discover_all_props()

        print(f"[JSBSim] Model '{model}' | "
              f"{self._engine_count} engine(s) | "
              f"{self._tank_count} tank(s) | "
              f"{len(self._all_props)} properties discovered")

        # ── Default controls ──────────────────────────────────────────
        ctrl = default_controls or {}
        self._throttle   = ctrl.get("throttle",   0.6)
        self._elevator   = ctrl.get("elevator",   0.0)
        self._aileron    = ctrl.get("aileron",    0.0)
        self._rudder     = ctrl.get("rudder",     0.0)
        self._flaps      = ctrl.get("flaps",      0.0)
        self._mixture    = ctrl.get("mixture",    0.9)
        self._speedbrake = ctrl.get("speedbrake", 0.0)
        self._gear       = ctrl.get("gear",       1.0)  # 1=down, 0=up

    # ─────────────────────────────────────────────────────────────────────────
    # Property discovery
    # ─────────────────────────────────────────────────────────────────────────

    def _discover_all_props(self) -> list:
        """
        Query the JSBSim property catalog for the loaded model and return
        every property path that:
          1. Belongs to a flight-relevant namespace (_CAPTURE_PREFIXES)
          2. Is actually readable without error

        This is called once at init — never hardcodes property names.
        """
        try:
            catalog_str = self.fdm.query_property_catalog("")
        except Exception:
            # Older JSBSim builds — fall back to an empty list;
            # derived values will still be computed.
            return []

        candidates = []
        for line in catalog_str.splitlines():
            parts = line.strip().split()
            if not parts:
                continue
            prop = parts[0]
            if any(prop.startswith(pfx) for pfx in _CAPTURE_PREFIXES):
                candidates.append(prop)

        # Verify each candidate is readable right now
        readable = []
        for prop in candidates:
            try:
                self.fdm[prop]
                readable.append(prop)
            except Exception:
                pass

        return readable

    # ─────────────────────────────────────────────────────────────────────────
    # Slot / engine detection
    # ─────────────────────────────────────────────────────────────────────────

    def _detect_slots(self, template: str, limit: int) -> int:
        count = 0
        for i in range(limit):
            try:
                self.fdm[template.format(i=i)]
                count += 1
            except Exception:
                break
        return count

    def _detect_engines(self, limit: int) -> int:
        count = 0
        for i in range(limit):
            found = any(
                self._g(f"propulsion/engine[{i}]/{p}") is not None
                for p in _ENGINE_PROBE
            )
            if found:
                count += 1
            else:
                break
        return count

    # ─────────────────────────────────────────────────────────────────────────
    # Safe property getters
    # ─────────────────────────────────────────────────────────────────────────

    def _g(self, prop):
        try:
            return self.fdm[prop]
        except Exception:
            return None

    def _gd(self, prop):
        v = self._g(prop)
        return math.degrees(v) if v is not None else None

    def _first(self, *props):
        for p in props:
            v = self._g(p)
            if v is not None:
                return v
        return None

    # ─────────────────────────────────────────────────────────────────────────
    # Simulation step
    # ─────────────────────────────────────────────────────────────────────────

    def step(self):
        def _s(p, v):
            try: self.fdm[p] = v
            except Exception: pass

        _s("fcs/throttle-cmd-norm",   self._throttle)
        _s("fcs/elevator-cmd-norm",   self._elevator)
        _s("fcs/aileron-cmd-norm",    self._aileron)
        _s("fcs/rudder-cmd-norm",     self._rudder)
        _s("fcs/flap-cmd-norm",       self._flaps)
        _s("fcs/mixture-cmd-norm",    self._mixture)
        _s("fcs/speedbrake-cmd-norm", self._speedbrake)
        _s("gear/gear-cmd-norm",      self._gear)
        self.fdm.run()

    def set_controls(self, throttle=None, elevator=None, aileron=None,
                     rudder=None, flaps=None, mixture=None,
                     speedbrake=None, gear=None):
        if throttle   is not None: self._throttle   = throttle
        if elevator   is not None: self._elevator   = elevator
        if aileron    is not None: self._aileron    = aileron
        if rudder     is not None: self._rudder     = rudder
        if flaps      is not None: self._flaps      = flaps
        if mixture    is not None: self._mixture    = mixture
        if speedbrake is not None: self._speedbrake = speedbrake
        if gear       is not None: self._gear       = gear

    # ─────────────────────────────────────────────────────────────────────────
    # Full state snapshot
    # ─────────────────────────────────────────────────────────────────────────

    def get_state(self) -> dict:
        """
        Returns a dict with three top-level sections:

          "jsbsim"  — every property the model exposes, keyed by JSBSim path.
                      Populated entirely from the catalog discovered at init.
                      Zero hardcoded property names here.

          "derived" — unit conversions and computed values not available
                      directly from JSBSim (deg conversions, hPa, density alt,
                      airspeed cross-check, etc.)

          "engines" — per-engine dict list  (length = engine_count)
          "tanks"   — per-tank dict list    (length = tank_count)
          "meta"    — model info and simulation timestamp
        """

        # ── 1. Read every discovered property ────────────────────────
        jsbsim_data = {}
        for prop in self._all_props:
            try:
                jsbsim_data[prop] = self.fdm[prop]
            except Exception:
                jsbsim_data[prop] = None

        # ── 2. Derived values (unit conversions + cross-checks) ───────
        g = self._g

        # Atmosphere conversions
        t_r   = g("atmosphere/T-R")
        p_psf = g("atmosphere/P-psf")
        rho   = g("atmosphere/rho-slugs_ft3")
        temp_c    = _R_TO_C(t_r) if t_r else None
        p_hpa     = p_psf * _PSF_TO_HPA if p_psf else None
        dens_alt  = (
            (1.0 - (rho / _RHO_SL) ** 0.235) / 6.87535e-6 if rho else None
        )

        # Velocity conversions
        hdot  = g("velocities/h-dot-fps")
        vg    = g("velocities/vg-fps")
        vs_fpm  = hdot * 60.0 if hdot is not None else None
        gs_kts  = vg * _FPS_TO_KTS if vg is not None else None

        # Wind in kts
        wn = g("atmosphere/wind-north-fps") or 0.0
        we = g("atmosphere/wind-east-fps")  or 0.0
        wd = g("atmosphere/wind-down-fps")  or 0.0
        wind_kts = math.sqrt(wn**2 + we**2 + wd**2) * _FPS_TO_KTS

        # Attitude in degrees (JSBSim stores in radians)
        pitch_deg   = self._gd("attitude/pitch-rad")
        roll_deg    = self._gd("attitude/roll-rad")
        alpha_deg   = self._gd("aero/alpha-rad")
        beta_deg    = self._gd("aero/beta-rad")
        alphadot    = self._gd("aero/alphadot-rad_sec")
        betadot     = self._gd("aero/betadot-rad_sec")
        roll_rate   = self._gd("velocities/p-rad_sec")
        pitch_rate  = self._gd("velocities/q-rad_sec")
        yaw_rate    = self._gd("velocities/r-rad_sec")
        roll_acc    = self._gd("accelerations/pdot-rad_sec2")
        pitch_acc   = self._gd("accelerations/qdot-rad_sec2")
        yaw_acc     = self._gd("accelerations/rdot-rad_sec2")

        # Airspeed cross-check for unreliable-airspeed detection
        tas = g("velocities/vtrue-kts")
        ias = g("velocities/vc-kts")
        eas = g("velocities/ve-kts")
        if tas is not None and ias is not None:
            delta_ti = abs(tas - ias)
            alt      = g("position/h-sl-ft") or 0
            suspect  = bool(
                (delta_ti > 30 and alt < 10000) or
                (eas is not None and abs(ias - eas) > 20)
            )
        else:
            delta_ti = None
            suspect  = None

        derived = {
            # Attitude (deg)
            "pitch_deg":              _r(pitch_deg,   2),
            "roll_deg":               _r(roll_deg,    2),   # = bank angle
            "heading_deg":            _r(g("attitude/psi-deg"), 2),
            # AoA / sideslip (deg)
            "alpha_deg":              _r(alpha_deg,   3),
            "beta_deg":               _r(beta_deg,    3),
            "alpha_dot_dps":          _r(alphadot,    3),
            "beta_dot_dps":           _r(betadot,     3),
            # Angular rates (deg/s)
            "roll_rate_dps":          _r(roll_rate,   3),
            "pitch_rate_dps":         _r(pitch_rate,  3),
            "yaw_rate_dps":           _r(yaw_rate,    3),
            "roll_accel_dps2":        _r(roll_acc,    3),
            "pitch_accel_dps2":       _r(pitch_acc,   3),
            "yaw_accel_dps2":         _r(yaw_acc,     3),
            # Velocities (human-friendly units)
            "vertical_speed_fpm":     _r(vs_fpm,      1),
            "groundspeed_kts":        _r(gs_kts,      2),
            # Atmosphere (SI / aviation units)
            "temp_c":                 _r(temp_c,      2),
            "pressure_hpa":           _r(p_hpa,       2),
            "density_alt_ft":         _r(dens_alt,    0),
            "speed_of_sound_kts":     _r((g("atmosphere/a-fps") or 0) * _FPS_TO_KTS, 1),
            "wind_north_kts":         _r(wn * _FPS_TO_KTS, 2),
            "wind_east_kts":          _r(we * _FPS_TO_KTS, 2),
            "wind_down_kts":          _r(wd * _FPS_TO_KTS, 2),
            "wind_total_kts":         _r(wind_kts,    2),
            # Airspeed cross-check
            "xcheck_delta_tas_ias_kts": _r(delta_ti,  2),
            "xcheck_airspeed_suspect":  suspect,
        }

        # ── 3. Per-engine structured data ─────────────────────────────
        engines = []
        for i in range(self._engine_count):
            ff = g(f"propulsion/engine[{i}]/fuel-flow-rate-pps")
            engines.append({
                "index":            i,
                "running":          g(f"propulsion/engine[{i}]/set-running"),
                # Piston
                "rpm":              _r(g(f"propulsion/engine[{i}]/engine-rpm"),        0),
                "map_inhg":         _r(g(f"propulsion/engine[{i}]/map-inhg"),           2),
                "cht_f":            _r(g(f"propulsion/engine[{i}]/cht-degF"),           1),
                "oil_pressure_psi": _r(g(f"propulsion/engine[{i}]/oil-pressure-psi"),  1),
                "oil_temp_f":       _r(g(f"propulsion/engine[{i}]/oil-temp-degF"),      1),
                # Turbine
                "n1_pct":           _r(g(f"propulsion/engine[{i}]/n1"),                 2),
                "n2_pct":           _r(g(f"propulsion/engine[{i}]/n2"),                 2),
                "thrust_lbs":       _r(g(f"propulsion/engine[{i}]/thrust-lbs"),         1),
                "egt_f":            _r(g(f"propulsion/engine[{i}]/egt-degF"),            1),
                # Common
                "fuel_flow_pph":    _r(ff * 3600 if ff is not None else None,          1),
            })

        # ── 4. Per-tank structured data ───────────────────────────────
        tanks = []
        total_fuel = 0.0
        for i in range(self._tank_count):
            c = g(f"propulsion/tank[{i}]/contents-lbs") or 0.0
            cap = g(f"propulsion/tank[{i}]/capacity-lbs")
            total_fuel += c
            tanks.append({
                "index":        i,
                "contents_lbs": _r(c,   2),
                "capacity_lbs": _r(cap, 1),
                "pct_full":     _r((c / cap * 100) if cap else None, 1),
            })

        # ── 5. Assemble final state ───────────────────────────────────
        return {
            "meta": {
                "model":          self.model,
                "engine_count":   self._engine_count,
                "tank_count":     self._tank_count,
                "prop_count":     len(self._all_props),
                "sim_time_s":     _r(g("simulation/sim-time-sec"), 3),
            },
            "jsbsim":  jsbsim_data,   # every property the model exposes
            "derived": derived,        # unit conversions + cross-checks
            "engines": engines,        # structured per-engine
            "tanks":   tanks,          # structured per-tank
            "fuel_total_lbs": _r(total_fuel, 2),
        }


# ── Module-level round helper ─────────────────────────────────────────────────
def _r(val, decimals: int):
    if val is None:
        return None
    try:
        return round(float(val), decimals)
    except (TypeError, ValueError):
        return val
