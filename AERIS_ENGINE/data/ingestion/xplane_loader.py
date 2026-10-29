"""Live feed from a running X-Plane 11/12.

X-Plane streams any dataref over UDP on request (an "RREF" subscription to
port 49000), so no plugin is needed: load a flight, then

    python main.py --source xplane [model] [--flight DEST_ICAO]

The aircraft profile is picked from the loaded aircraft's ICAO type unless a
model is given. Faults come from X-Plane's own failure menu, which the flight
model propagates properly. The fuel injectors drain gen.fuel_* each tick, but
the next tick reads the tanks back from X-Plane, so a leak never builds up.

Like ntsb_fdr_loader, what X-Plane does not report directly is left out of the
state rather than invented (control surface angles, engine efficiency).
"""

import math
import socket
import struct
import time

from data.ingestion.flight_common import Phase, haversine_nm

_KG_TO_LB = 2.204623
_MS_TO_KT = 1.943844
_M_TO_FT = 3.28084
_INHG_TO_HPA = 33.8639
_FT_PER_INHG = 1000.0
_MAX_ENGINES = 4
_LOST_AFTER_S = 5.0   # no packet for this long = X-Plane quit or unloaded the flight

_REFS = [
    ("lat",          "sim/flightmodel/position/latitude"),
    ("lon",          "sim/flightmodel/position/longitude"),
    ("elev_m",       "sim/flightmodel/position/elevation"),
    ("agl_m",        "sim/flightmodel/position/y_agl"),
    ("radio_alt",    "sim/cockpit2/gauges/indicators/radio_altimeter_height_ft_pilot"),
    ("gs_ms",        "sim/flightmodel/position/groundspeed"),
    ("tas_ms",       "sim/flightmodel/position/true_airspeed"),
    ("cas",          "sim/flightmodel/position/indicated_airspeed"),
    ("mach",         "sim/flightmodel/misc/machno"),
    ("ias_cap",      "sim/cockpit2/gauges/indicators/airspeed_kts_pilot"),
    ("ias_fo",       "sim/cockpit2/gauges/indicators/airspeed_kts_copilot"),
    ("alt_cap",      "sim/cockpit2/gauges/indicators/altitude_ft_pilot"),
    ("alt_fo",       "sim/cockpit2/gauges/indicators/altitude_ft_copilot"),
    ("vs_cap",       "sim/cockpit2/gauges/indicators/vvi_fpm_pilot"),
    ("vs_fo",        "sim/cockpit2/gauges/indicators/vvi_fpm_copilot"),
    ("pitch_cap",    "sim/cockpit2/gauges/indicators/pitch_AHARS_deg_pilot"),
    ("pitch_fo",     "sim/cockpit2/gauges/indicators/pitch_AHARS_deg_copilot"),
    ("roll_cap",     "sim/cockpit2/gauges/indicators/roll_AHARS_deg_pilot"),
    ("roll_fo",      "sim/cockpit2/gauges/indicators/roll_AHARS_deg_copilot"),
    ("hdg_cap",      "sim/cockpit2/gauges/indicators/heading_AHARS_deg_mag_pilot"),
    ("hdg_fo",       "sim/cockpit2/gauges/indicators/heading_AHARS_deg_mag_copilot"),
    ("pitch",        "sim/flightmodel/position/theta"),
    ("roll",         "sim/flightmodel/position/phi"),
    ("heading",      "sim/flightmodel/position/psi"),
    ("aoa",          "sim/flightmodel/position/alpha"),
    ("fpa",          "sim/flightmodel/position/vpath"),
    ("track",        "sim/flightmodel/position/hpath"),
    ("g",            "sim/flightmodel/forces/g_nrml"),
    ("oat",          "sim/cockpit2/temperature/outside_air_temp_degc"),
    ("tat",          "sim/cockpit2/temperature/outside_air_LE_temp_degc"),
    ("slp_pa",       "sim/weather/region/sealevel_pressure_pas"),
    ("baro_cap",     "sim/cockpit2/gauges/actuators/barometer_setting_in_hg_pilot"),
    ("baro_fo",      "sim/cockpit2/gauges/actuators/barometer_setting_in_hg_copilot"),
    ("wind_dir",     "sim/cockpit2/gauges/indicators/wind_heading_deg_mag"),
    ("wind_spd",     "sim/cockpit2/gauges/indicators/wind_speed_kts"),
    ("throttle",     "sim/cockpit2/engine/actuators/throttle_ratio_all"),
    ("reverser",     "sim/flightmodel2/engines/thrust_reverser_deploy_ratio[0]"),
    ("n_engines",    "sim/aircraft/engine/acf_num_engines"),
    ("fuel_total",   "sim/flightmodel/weight/m_fuel_total"),
    ("fuel_left",    "sim/flightmodel/weight/m_fuel[0]"),
    ("fuel_right",   "sim/flightmodel/weight/m_fuel[2]"),
    ("weight",       "sim/flightmodel/weight/m_total"),
    ("flap",         "sim/flightmodel2/controls/flap1_deploy_ratio"),
    ("gear",         "sim/flightmodel2/gear/deploy_ratio[0]"),
    ("speedbrake",   "sim/cockpit2/controls/speedbrake_ratio"),
    ("park_brake",   "sim/cockpit2/controls/parking_brake_ratio"),
    ("cabin_alt",    "sim/cockpit2/pressurization/indicators/cabin_altitude_ft"),
    ("cabin_diff",   "sim/cockpit2/pressurization/indicators/pressure_diffential_psi"),
    ("ap_servos",    "sim/cockpit2/autopilot/servos_on"),
    ("anti_ice",     "sim/cockpit2/ice/ice_inlet_heat_on_per_engine[0]"),
    ("ice",          "sim/flightmodel/failures/frm_ice"),
    ("stall_warn",   "sim/cockpit2/annunciators/stall_warning"),
    ("on_ground",    "sim/flightmodel/failures/onground_any"),
    ("paused",       "sim/time/paused"),
] + [
    (f"{k}{i}", f"sim/flightmodel/engine/{ref}[{i}]")
    for i in range(_MAX_ENGINES)
    for k, ref in (("n1_", "ENGN_N1_"), ("n2_", "ENGN_N2_"),
                   ("egt_", "ENGN_EGT_c"), ("ff_", "ENGN_FF_"))
] + [(f"icao{i}", f"sim/aircraft/view/acf_ICAO[{i}]") for i in range(4)]

# Failures set from X-Plane's failure menu, read back as training labels.
# failure_enum: 0 working, 1-5 armed (time/speed/altitude/key triggers), 6 failed.
_FAILED = 6
_ENGINE_FAILURES = (("engine_fire", "rel_engfir"), ("engine_flameout", "rel_engfla"),
                    ("engine_failure", "rel_engfai"), ("engine_separation", "rel_engsep"),
                    ("compressor_stall", "rel_comsta"), ("fuel_flow_fluctuation", "rel_fuelfl"))
_FAILURES = [(f"{label}_{i + 1}", f"{ref}{i}")
             for label, ref in _ENGINE_FAILURES for i in range(_MAX_ENGINES)] + [
    ("pitot_blocked_1", "rel_pitot"), ("pitot_blocked_2", "rel_pitot2"),
    ("pitot_blocked_stby", "rel_pitot_stby"),
    ("static_blocked_1", "rel_static"), ("static_blocked_2", "rel_static2"),
    ("static_blocked_stby", "rel_static_stby"),
    ("pitot_heat_1", "rel_ice_pitot_heat1"), ("pitot_heat_2", "rel_ice_pitot_heat2"),
    ("aoa_sensor", "rel_AOA"), ("stall_warning", "rel_stall_warn"),
    ("decompression_slow", "rel_depres_slow"), ("decompression_fast", "rel_depres_fast"),
    ("fuel_leak", "rel_fuel_leak"), ("fuel_water", "rel_fuel_water"),
    ("hydraulic_pump_1", "rel_hydpmp"), ("hydraulic_pump_2", "rel_hydpmp2"),
    ("hydraulic_pump_3", "rel_hydpmp3"),
    ("hydraulic_leak_1", "rel_hydleak"), ("hydraulic_leak_2", "rel_hydleak2"),
    ("hydraulic_leak_3", "rel_hydleak3"),
    ("generator_1", "rel_genera0"), ("generator_2", "rel_genera1"),
    ("electrical_total", "rel_gen_esys"), ("smoke_cockpit", "rel_smoke_cpit"),
    ("gear_actuator", "rel_gear_act"), ("flap_actuator", "rel_flap_act"),
    ("wing_anti_ice", "rel_ice_surf_heat"),
    ("engine_anti_ice_1", "rel_ice_inlet_heat"), ("engine_anti_ice_2", "rel_ice_inlet_heat2"),
    ("tire_blowout_1", "rel_tire1"), ("tire_blowout_2", "rel_tire2"),
]
_REFS += [(f"fail:{label}", f"sim/operation/failures/{ref}") for label, ref in _FAILURES]

# X-Plane ICAO type prefix -> closest AERIS performance profile.
_ICAO_PERF = (("A33", "A330-223"), ("A34", "A330-223"), ("A3", "A320"), ("A2", "A320"),
              ("B73", "737"), ("B77", "787-8"), ("B78", "787-8"), ("B74", "B747"),
              ("C13", "C130"), ("C17", "c172p"))


def parse_rref(packet: bytes) -> dict[int, float]:
    """RREF reply -> {subscription index: value}. Header is b'RREF' plus one byte."""
    if not packet.startswith(b"RREF"):
        return {}
    body = packet[5:]
    return dict(struct.unpack_from("<if", body, off) for off in range(0, len(body) - 7, 8))


def perf_model_for(icao: str) -> str:
    for prefix, model in _ICAO_PERF:
        if icao.upper().startswith(prefix):
            return model
    return "737"


def flight_phase_label(prev: str, on_ground: bool, was_airborne: bool, gs_kts: float,
                       throttle: float, park_brake: bool, agl_ft: float, vs_fpm: float,
                       gear_down: bool, flaps_out: bool, level_s: float) -> str:
    """Fine-grained phase for training labels. `phase` stays the coarse Phase
    the monitoring modules were tuned on; this one is never read by them."""
    if on_ground:
        if was_airborne:
            return "LANDING" if gs_kts > 40 else "TAXI"
        if prev in ("TAKEOFF_ROLL", "REJECTED_TAKEOFF") and gs_kts > 40 and throttle < 0.3:
            return "REJECTED_TAKEOFF"
        if gs_kts > 40 and throttle > 0.5:
            return "TAKEOFF_ROLL"
        if prev == "REJECTED_TAKEOFF" and gs_kts > 5:
            return "REJECTED_TAKEOFF"
        return "PARKED" if gs_kts < 1 and park_brake else "TAXI"

    if prev in ("APPROACH", "LANDING", "GO_AROUND") and vs_fpm > 500 and throttle > 0.7 \
            or prev == "GO_AROUND" and vs_fpm > 0 and agl_ft < 3000:
        return "GO_AROUND"
    if prev in ("APPROACH", "LANDING") and agl_ft < 50 and vs_fpm < 300:
        return "LANDING"
    if (gear_down or flaps_out) and agl_ft < 3000 and vs_fpm < 300 \
            and prev in ("DESCENT", "APPROACH", "CRUISE"):
        return "APPROACH"
    if vs_fpm > 300:
        return "INITIAL_CLIMB" if agl_ft < 1500 and prev in ("TAKEOFF_ROLL", "INITIAL_CLIMB") \
            else "CLIMB"
    if vs_fpm < -300:
        return "DESCENT"
    if level_s >= 60:
        return "CRUISE"
    # Brief level-offs keep the phase they interrupt.
    return prev if prev in ("INITIAL_CLIMB", "CLIMB", "DESCENT", "CRUISE", "APPROACH") else "CLIMB"


def flight_phase(on_ground: bool, was_airborne: bool, agl_ft: float, vs_fpm: float,
                 gear_down: bool) -> Phase:
    if on_ground:
        return Phase.LANDING if was_airborne else Phase.GROUND_ROLL
    if vs_fpm > 300:
        return Phase.CLIMB
    if vs_fpm < -300:
        return Phase.LANDING if gear_down and agl_ft < 2000 else Phase.DESCENT
    return Phase.CRUISE


class XPlaneLive:
    """Same surface as NtsbReplay, fed from X-Plane instead of a recording."""

    def __init__(self, perf=None, airports=None, dest_icao=None, emergency_fn=None,
                 host="127.0.0.1", port=49000, rate_hz=10.0, connect_timeout_s=15.0):
        self.xp = (host, port)
        self.emergency_fn = emergency_fn
        self.meta = {}
        self.dt = 1.0 / rate_hz
        self.time = 0.0
        self.phase = Phase.GROUND_ROLL
        self._was_airborne = False
        self._last_log_t = -1e9
        self._log_interval = 60.0
        self._v: dict[str, float] = {}

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(("", 0))
        self._connect(connect_timeout_s)

        self.acf_icao = "".join(chr(int(self._v.get(f"icao{i}", 0))) for i in range(4)).strip("\0 ")
        if perf is None:
            from data.ingestion.aircraft_performance import get_performance
            perf = get_performance(perf_model_for(self.acf_icao), airport_elev_ft=0.0)
        self.perf = perf

        v = self._v
        self.lat, self.lon = v["lat"], v["lon"]
        self.heading = v["heading"]
        self.fuel_total_lbs = v["fuel_total"] * _KG_TO_LB
        self._initial_fuel_lbs = max(1.0, self.fuel_total_lbs)
        self.fuel = 100.0
        self.fuel_used_lbs = 0.0
        self.fuel_left_lbs = self.fuel_right_lbs = self.fuel_center_lbs = 0.0
        self.fuel_flow_total_lbs_hr = 0.0

        self.airports = airports if airports is not None else _load_all_airports()
        fallback = {"icao": "", "iata": "", "lat": 0.0, "lon": 0.0, "name": ""}
        self.origin = self._nearest_airport(self.lat, self.lon) or fallback
        self.dest = next((a for a in self.airports if a.get("icao") == (dest_icao or "").upper()),
                         None) or fallback
        self.region = "EU" if self.origin.get("icao", "")[:1] in ("E", "L") else "US"
        self.route_distance_nm = (
            haversine_nm(self.origin["lat"], self.origin["lon"], self.dest["lat"], self.dest["lon"])
            if self.origin.get("lat") and self.dest.get("lat") else 0.0)
        self.departure_heading = self.landing_heading = self.heading
        self._cruise_speed = 450.0
        self.alternates = []

    # ── UDP ─────────────────────────────────────────────────────────────

    def _subscribe(self, hz: int) -> None:
        for i, (_, ref) in enumerate(_REFS):
            self._sock.sendto(b"RREF\0" + struct.pack("<ii400s", hz, i, ref.encode()), self.xp)

    def _connect(self, timeout_s: float) -> None:
        """X-Plane ignores requests while a flight is still loading, so keep
        asking until every subscribed value has arrived once."""
        self._sock.settimeout(1.0)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self._subscribe(int(2 / self.dt))
            try:
                self._absorb(self._sock.recvfrom(65536)[0])
            except (socket.timeout, ConnectionResetError):
                continue
            if len(self._v) == len(_REFS):
                break
        if "lat" not in self._v:
            self._sock.close()
            raise ConnectionError(
                f"No answer from X-Plane at {self.xp[0]}:{self.xp[1]}. "
                f"Start X-Plane and load a flight first.")
        missing = [k for k, _ in _REFS if k not in self._v]
        if missing:
            print(f"[XPLANE] No value yet for {missing}; reading them as 0")
            self._v.update(dict.fromkeys(missing, 0.0))
        self._sock.setblocking(False)
        self._last_packet = time.monotonic()

    def _absorb(self, packet: bytes) -> None:
        for idx, val in parse_rref(packet).items():
            if 0 <= idx < len(_REFS):
                self._v[_REFS[idx][0]] = val

    def _drain(self) -> None:
        while True:
            try:
                self._absorb(self._sock.recvfrom(65536)[0])
                self._last_packet = time.monotonic()
            except (BlockingIOError, ConnectionResetError):
                return

    def close(self) -> None:
        try:
            self._subscribe(0)
        finally:
            self._sock.close()

    # ── metadata ────────────────────────────────────────────────────────

    def _nearest_airport(self, lat, lon):
        best, best_d = None, 10.0
        for a in self.airports:
            if a.get("lat") is None:
                continue
            d = haversine_nm(lat, lon, a["lat"], a["lon"])
            if d < best_d:
                best, best_d = a, d
        return best

    # ── live ────────────────────────────────────────────────────────────

    def step(self) -> dict:
        self._drain()
        v = self._v
        n_eng = max(1, min(_MAX_ENGINES, int(v.get("n_engines") or self.perf.engine_count)))
        eng = lambda k, scale=1.0: [round(v[f"{k}{i}"] * scale, 1) for i in range(n_eng)]

        on_ground = v["on_ground"] > 0.5
        agl_ft = v["agl_m"] * _M_TO_FT
        vs = v["vs_cap"]
        gear_down = v["gear"] > 0.5
        self._was_airborne |= not on_ground and agl_ft > 50
        self.phase = flight_phase(on_ground, self._was_airborne, agl_ft, vs, gear_down)

        alt = v["alt_cap"]
        pres_alt = alt + (29.92 - v["baro_cap"]) * _FT_PER_INHG
        isa_c = 15.0 - 1.98 * pres_alt / 1000.0
        wind_rel = math.radians(v["wind_dir"] - v["hdg_cap"])

        # Left wing tank is [0] and right [2] on Laminar's airliners; the rest
        # (centre, trim, aux) is lumped into centre.
        self.fuel_total_lbs = v["fuel_total"] * _KG_TO_LB
        self.fuel_left_lbs = v["fuel_left"] * _KG_TO_LB
        self.fuel_right_lbs = v["fuel_right"] * _KG_TO_LB
        self.fuel_center_lbs = max(0.0, self.fuel_total_lbs - self.fuel_left_lbs - self.fuel_right_lbs)
        self.fuel_used_lbs = max(0.0, self._initial_fuel_lbs - self.fuel_total_lbs)
        self.fuel = self.fuel_total_lbs / self._initial_fuel_lbs * 100.0
        ff = eng("ff_", _KG_TO_LB * 3600.0)
        self.fuel_flow_total_lbs_hr = sum(ff)

        self.lat, self.lon, self.heading = v["lat"], v["lon"], v["heading"]
        ap_on = v["ap_servos"] > 0.5
        p = self.perf
        dest = self.dest

        state = {
            "time": round(self.time, 3),
            "phase": self.phase.name,
            "dt": self.dt,
            "sample_rate_hz": round(1.0 / self.dt, 4),
            "source": "xplane",
            "sim_paused": v["paused"] > 0.5,

            "lat": round(v["lat"], 6), "lon": round(v["lon"], 6),
            "groundspeed_kts": round(v["gs_ms"] * _MS_TO_KT, 1),
            "tas_kts": round(v["tas_ms"] * _MS_TO_KT, 1),
            "cas_kts": round(v["cas"], 1),
            "mach": round(v["mach"], 3),

            "ias_captain": round(v["ias_cap"], 2), "ias_fo": round(v["ias_fo"], 2),
            "alt_captain": round(alt, 1), "alt_fo": round(v["alt_fo"], 1),
            "vs_captain": round(vs, 1), "vs_fo": round(v["vs_fo"], 1),
            "pitch_captain": round(v["pitch_cap"], 3), "pitch_fo": round(v["pitch_fo"], 3),
            "roll_captain": round(v["roll_cap"], 3), "roll_fo": round(v["roll_fo"], 3),
            "heading_captain": round(v["hdg_cap"], 2), "heading_fo": round(v["hdg_fo"], 2),

            "pitch_deg": round(v["pitch"], 2), "roll_deg": round(v["roll"], 2),
            "heading_deg": round(v["heading"], 1),
            "aoa_deg": round(v["aoa"], 2), "fpa_deg": round(v["fpa"], 2),
            "load_factor_g": round(v["g"], 3),
            "track_deg": round(v["track"], 1),
            "radio_alt_ft": round(v["radio_alt"], 1),
            "weight_on_wheels": int(on_ground),

            "oat_c": round(v["oat"], 1), "tat_c": round(v["tat"], 1),
            "qnh_hpa": round(v["slp_pa"] / 100.0, 1),
            "pressure_alt_ft": round(pres_alt),
            "density_alt_ft": round(pres_alt + 118.8 * (v["oat"] - isa_c)),
            "baro_setting_captain_hpa": round(v["baro_cap"] * _INHG_TO_HPA, 1),
            "baro_setting_fo_hpa": round(v["baro_fo"] * _INHG_TO_HPA, 1),

            "wind_dir_deg": round(v["wind_dir"], 1), "wind_spd_kts": round(v["wind_spd"], 1),
            "headwind_kts": round(v["wind_spd"] * math.cos(wind_rel), 1),
            "crosswind_kts": round(v["wind_spd"] * math.sin(wind_rel), 1),

            "throttle_pct": round(v["throttle"] * 100.0, 1),
            "n1_pct": eng("n1_"), "n2_pct": eng("n2_"), "egt_c": eng("egt_"),
            "fuel_flow_pph": ff,
            "reverse_active": v["reverser"] > 0.1,

            "fuel_pct": round(self.fuel, 2),
            "fuel_lbs": round(self.fuel_total_lbs),
            "gross_weight_lbs": round(v["weight"] * _KG_TO_LB),
            "fuel_available": True,
            "fuel_total_lbs": round(self.fuel_total_lbs, 1),
            "fuel_used_lbs": round(self.fuel_used_lbs, 1),
            "fuel_left_lbs": round(self.fuel_left_lbs, 1),
            "fuel_right_lbs": round(self.fuel_right_lbs, 1),
            "fuel_center_lbs": round(self.fuel_center_lbs, 1),
            "fuel_flow_total_lbs_hr": round(self.fuel_flow_total_lbs_hr, 1),
            "fuel_flow_eng1_lbs_hr": ff[0],
            "fuel_flow_eng2_lbs_hr": ff[1] if n_eng > 1 else 0.0,
            "fuel_flow_per_eng_lbs_hr": ff,
            # 30 min holding: the ICAO final reserve.
            "fuel_reserve_min_lbs": round(p.ff_holding_lbs_hr * p.engine_count * 0.5, 1),

            # shortcut: flap ratio x the profile's full landing flap, wrong for
            # aircraft whose detents aren't linear; read flap1_deg if that matters.
            "flap_deg": round(v["flap"] * p.flap_land_deg, 1),
            "gear": "DOWN" if v["gear"] > 0.99 else "UP" if v["gear"] < 0.01 else "TRANSIT",
            "gear_down_locked": int(v["gear"] > 0.99),
            "speedbrake": round(v["speedbrake"], 2),
            "brake_pct": round(v["park_brake"] * 100.0, 1),

            "cabin_alt_ft": round(v["cabin_alt"]),
            "cabin_diff_psi": round(v["cabin_diff"], 2),

            "autopilot": ap_on, "ap_engaged": int(ap_on),
            "ap_mode": ("DES" if vs < -300 else "CLB" if vs > 300 else "CRZ") if ap_on else "OFF",
            "anti_ice": v["anti_ice"] > 0.5,
            "airframe_ice_ratio": round(v["ice"], 3),
            "stall_warning": v["stall_warn"] > 0.5,

            "v1_kts": p.v1_kts, "vr_kts": p.vr_kts, "v2_kts": p.v2_kts,
            "vref_kts": p.vref_kts, "best_glide_kts": p.best_glide_kts,

            "origin_icao": self.origin.get("icao", ""),
            "destination_icao": dest.get("icao", ""),
            "dist_to_dest_nm": round(haversine_nm(v["lat"], v["lon"], dest["lat"], dest["lon"]), 1)
            if dest.get("lat") else 0.0,
        }

        if self.emergency_fn:
            self.emergency_fn(state, self)

        if not state["sim_paused"]:
            self.time += self.dt
        return state

    def should_log(self) -> bool:
        if self.phase == Phase.CRUISE and self.time - self._last_log_t < self._log_interval:
            return False
        self._last_log_t = self.time
        return True

    @property
    def finished(self) -> bool:
        """A live feed ends when X-Plane stops answering."""
        if time.monotonic() - self._last_packet > _LOST_AFTER_S:
            self.close()
            return True
        return False


def _load_all_airports() -> list[dict]:
    """X-Plane can be flown anywhere, so take both the FAA and Eurocontrol
    lists; either may be missing when the research drive isn't connected."""
    import importlib

    airports = []
    for mod in ("faa_airport_loader", "eurocontrol_airport_loader"):
        try:
            airports += importlib.import_module(f"data.ingestion.{mod}").load_airports()
        except Exception as exc:  # noqa: BLE001 — a missing list only costs origin/dest names
            print(f"[XPLANE] {mod} unavailable: {exc}")
    return airports
