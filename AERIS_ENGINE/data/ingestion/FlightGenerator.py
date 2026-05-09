import asyncio
import json
import random
import math
import csv
import time
import os
from enum import Enum, auto


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def move(lat, lon, heading, distance_km):
    R = 6371
    heading = math.radians(heading)
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)
    lat2 = math.asin(
        math.sin(lat1) * math.cos(distance_km / R) +
        math.cos(lat1) * math.sin(distance_km / R) * math.cos(heading)
    )
    lon2 = lon1 + math.atan2(
        math.sin(heading) * math.sin(distance_km / R) * math.cos(lat1),
        math.cos(distance_km / R) - math.sin(lat1) * math.sin(lat2)
    )
    return math.degrees(lat2), math.degrees(lon2)


def bearing(lat1, lon1, lat2, lon2):
    dlon = math.radians(lon2 - lon1)
    y = math.sin(dlon) * math.cos(math.radians(lat2))
    x = (math.cos(math.radians(lat1)) * math.sin(math.radians(lat2)) -
         math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.cos(dlon))
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 3440.065
    lat1, lat2 = math.radians(lat1), math.radians(lat2)
    dlat = lat2 - lat1
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


# ─────────────────────────────────────────────
# Merge / build helpers (used by standalone runner)
# ─────────────────────────────────────────────
def merge_flight_logs(model, start_time, parts, cleanup=True):
    final_name = f"flightData_{model}_{start_time}_FULL.jsonl"
    with open(final_name, "w") as outfile:
        for p in range(1, parts + 1):
            part_file = f"flightData_{model}_{start_time}_part{p}.jsonl"
            try:
                with open(part_file, "r") as infile:
                    for line in infile:
                        outfile.write(line)
            except FileNotFoundError:
                print(f"Missing {part_file}, skipping")
    if cleanup:
        for p in range(1, parts + 1):
            part_file = f"flightData_{model}_{start_time}_part{p}.jsonl"
            if os.path.exists(part_file):
                os.remove(part_file)
    print(f"Merged: {final_name}")
    return final_name


def build_final_output(model, start_time, jsonl_file, meta):
    final_json = f"flightData_{model}_{start_time}.json"
    simulation = []
    with open(jsonl_file, "r") as f:
        for line in f:
            simulation.append(json.loads(line))
    meta["duration"] = simulation[-1]["time"] if simulation else 0
    meta["remaining_fuel_lbs"] = simulation[-1]["fuel_lbs"] if simulation else 0
    final = {"meta": meta, "simulation": simulation}
    with open(final_json, "w") as f:
        json.dump(final, f)
    print(f"Final JSON created: {final_json}")


# ─────────────────────────────────────────────
# Phase enum
# ─────────────────────────────────────────────
class Phase(Enum):
    GROUND_ROLL = auto()
    ROTATION    = auto()
    CLIMB       = auto()
    CRUISE      = auto()
    DESCENT     = auto()
    LANDING     = auto()
    COMPLETE    = auto()


_CRITICAL_PHASES = frozenset({
    Phase.GROUND_ROLL, Phase.ROTATION, Phase.CLIMB, Phase.DESCENT, Phase.LANDING
})


# ─────────────────────────────────────────────
# Flight Generator
# ─────────────────────────────────────────────
class FlightGenerator:

    def __init__(self, perf, airport_csv, dt=1/30, emergency_fn=None):
        self.perf         = perf
        self.dt           = dt
        self.emergency_fn = emergency_fn

        self.time  = 0.0
        self.phase = Phase.GROUND_ROLL

        self.alt     = 0.0
        self.speed   = 0.0
        self.pitch   = 0.0
        self.roll    = 0.0
        self.heading = 0.0
        self.vs      = 0.0

        self.initial_fuel  = 100.0
        self.fuel          = self.initial_fuel   # percent remaining
        self.fuel_leak_rate = 0.0

        # ── Derived dynamics ───────────────────────────────────────────
        t2w = perf.thrust_total_lbs / perf.tow_lbs

        self._ground_accel = clamp(t2w * 3.0, 0.3, 1.5)
        self._ground_decel = self._ground_accel * 0.9

        vs_raw = perf.climb_speed_kts * math.sin(math.radians(perf.climb_pitch)) * 101.269
        self._vs_climb   = clamp(vs_raw, 300.0, 2500.0)
        self._vs_descent = -2000.0 if perf.is_transport else -800.0

        self._cruise_speed = (280.0 if perf.is_transport
                              else clamp(perf.climb_speed_kts * 1.1, 60.0, 130.0))

        self._fuel_rate_cruise = t2w * 0.12   # % per second at cruise multiplier 1.0

        # ── Airports ───────────────────────────────────────────────────
        self.airports = self._load_airports(airport_csv)
        self.origin, self.dest, self.route_distance_nm = self._pick_airports(perf)

        self.lat = self.origin["lat"]
        self.lon = self.origin["lon"]

        self.target_heading = bearing(
            self.lat, self.lon, self.dest["lat"], self.dest["lon"]
        )
        self.departure_heading    = random.choice(range(0, 360, 10)) * 1.0
        self._initial_turn_done   = False
        self.heading              = self.departure_heading

        self.cruise_alt = min(perf.cruise_alt_ft, 30000)
        if perf.model == "c172p":
            self.cruise_alt = random.randint(5000, 10000)

        # ── Throttle / engines ────────────────────────────────────────
        self.throttle = 0.0   # 0–1 forward thrust lever position
        self.toga     = False
        self.trim     = 0.0   # normalised elevator trim −1 … +1
        self.engines  = [0.0] * perf.engine_count

        # ── Flight controls state ─────────────────────────────────────
        self.flap_deg          = perf.flap_to_deg    # current flap angle (degrees)
        self.gear              = "DOWN"
        self._gear_transit_t   = -1.0
        self.speedbrake        = 0.0                 # 0 = stowed, 1 = full
        self.reverse_active    = False
        self.autopilot         = False
        self.ap_mode           = "OFF"

        # ── Atmosphere (fixed per simulation, slight variation) ────────
        self.qnh_hpa       = round(random.uniform(990.0, 1033.0), 1)

        # ── Wind (constant direction/speed, held throughout) ──────────
        self.wind_dir      = round(random.uniform(0, 359))
        self.wind_spd_kts  = round(random.uniform(0, 30), 1)

        # ── Logging cadence ───────────────────────────────────────────
        self._log_interval      = clamp(60 + (self.route_distance_nm / 5000) * 120, 60, 180)
        self._last_log_time     = -999.0
        self._prev_log_phase    = None
        self._prev_log_throttle = -1.0
        self._prev_log_heading  = self.heading
        self._prev_log_vs_sign  = 0
        self._prev_log_alt      = self.alt
        self._prev_log_speed    = self.speed

        self.meta = {
            "model":             perf.model,
            "origin":            self.origin,
            "destination":       self.dest,
            "route_distance_nm": round(self.route_distance_nm, 1),
            "initial_fuel_lbs":  perf.fuel_capacity_lbs,
        }

    # ── Airport helpers ────────────────────────────────────────────────
    def _load_airports(self, path):
        airports = []
        with open(path, encoding="utf-8", errors="replace") as f:
            for r in csv.DictReader(f):
                if not r.get("latitude_deg") or not r.get("longitude_deg"):
                    continue
                if r.get("type", "") in ("heliport", "closed"):
                    continue
                try:
                    lat = float(r["latitude_deg"])
                    lon = float(r["longitude_deg"])
                except ValueError:
                    continue
                airports.append({
                    "lat":       lat,
                    "lon":       lon,
                    "icao":      (r.get("ident") or r.get("icao_code") or "").strip(),
                    "iata":      r.get("iata_code", "").strip(),
                    "name":      r.get("name", "").strip(),
                    "scheduled": r.get("scheduled_service", "no") == "yes",
                })
        return airports

    def _pick_airports(self, perf):
        pool = [a for a in self.airports if a["scheduled"]] if perf.is_transport else self.airports
        if len(pool) < 10:
            pool = self.airports

        origin = random.choice(pool)
        sample = random.sample(pool, min(3000, len(pool)))

        valid = []
        for apt in sample:
            if apt["icao"] == origin["icao"]:
                continue
            d = haversine_nm(origin["lat"], origin["lon"], apt["lat"], apt["lon"])
            if perf.min_route_nm <= d <= perf.max_route_nm:
                valid.append((apt, d))

        if valid:
            dest, dist = random.choice(valid)
        else:
            dest = random.choice([a for a in pool if a is not origin])
            dist = haversine_nm(origin["lat"], origin["lon"], dest["lat"], dest["lon"])

        return origin, dest, dist

    # ── Utilities ─────────────────────────────────────────────────────
    def _noise(self, v, n):
        return v + random.uniform(-n, n)

    def _phase_fuel_mult(self) -> float:
        return {
            Phase.GROUND_ROLL: 1.2,
            Phase.ROTATION:    1.3,
            Phase.CLIMB:       1.5,
            Phase.CRUISE:      1.0,
            Phase.DESCENT:     0.6,
            Phase.LANDING:     0.3,
        }.get(self.phase, 1.0)

    # ── Logging gate ──────────────────────────────────────────────────
    def should_log(self) -> bool:
        """Critical phases: event-driven (no time floor).
        Cruise: fixed interval scaled 60 s–180 s by route distance."""
        vs_sign     = 1 if self.vs > 50 else (-1 if self.vs < -50 else 0)
        hdg_delta   = abs((self.heading - self._prev_log_heading + 180) % 360 - 180)
        alt_delta   = abs(self.alt   - self._prev_log_alt)
        speed_delta = abs(self.speed - self._prev_log_speed)

        if self.phase in _CRITICAL_PHASES:
            triggered = (
                self.phase  != self._prev_log_phase
                or hdg_delta   > 5.0
                or vs_sign     != self._prev_log_vs_sign
                or alt_delta   > 300.0
                or speed_delta > 5.0
                or abs(self.throttle - self._prev_log_throttle) > 0.05
            )
        else:
            triggered = (self.time - self._last_log_time) >= self._log_interval

        if triggered:
            self._last_log_time     = self.time
            self._prev_log_phase    = self.phase
            self._prev_log_throttle = self.throttle
            self._prev_log_heading  = self.heading
            self._prev_log_vs_sign  = vs_sign
            self._prev_log_alt      = self.alt
            self._prev_log_speed    = self.speed

        return triggered

    # ── Phase transitions ─────────────────────────────────────────────
    def _update_phase(self):
        if   self.phase == Phase.GROUND_ROLL and self.speed >= self.perf.vr_kts:
            self.phase = Phase.ROTATION
        elif self.phase == Phase.ROTATION    and self.alt > 50:
            self.phase = Phase.CLIMB
        elif self.phase == Phase.CLIMB       and self.alt >= self.cruise_alt:
            self.phase = Phase.CRUISE
        elif self.phase == Phase.CRUISE      and self.fuel < 30:
            self.phase = Phase.DESCENT
        elif self.phase == Phase.DESCENT     and self.alt <= 100:
            self.phase = Phase.LANDING
        elif self.phase == Phase.LANDING     and self.speed <= 5.0:
            self.phase = Phase.COMPLETE

    # ── Throttle schedule (realistic) ─────────────────────────────────
    def _pilot_inputs(self):
        p = self.perf

        if self.phase == Phase.GROUND_ROLL:
            self.throttle = 1.0                        # TOGA
        elif self.phase == Phase.ROTATION:
            self.throttle = 1.0                        # TOGA
        elif self.phase == Phase.CLIMB:
            if self.alt < 1500:
                self.throttle = 1.0                    # TOGA to thrust-reduction altitude
            else:
                # CLB thrust: slight reduction with altitude
                self.throttle = clamp(0.88 - self.alt * 0.000003, 0.78, 0.88)
        elif self.phase == Phase.CRUISE:
            # Autothrottle: hold cruise speed
            speed_err = self._cruise_speed - self.speed
            self.throttle = clamp(0.72 + speed_err * 0.003, 0.62, 0.82)
        elif self.phase == Phase.DESCENT:
            self.throttle = 0.07                       # idle descent
        elif self.phase == Phase.LANDING:
            # Idle on approach/flare; ground-idle once on runway
            self.throttle = 0.05
        else:
            self.throttle = 0.72

        self.toga    = self.phase in (Phase.GROUND_ROLL, Phase.ROTATION)
        self.trim    = clamp(self.pitch * 0.1, -1.0, 1.0)
        self.engines = [self.throttle] * p.engine_count

    # ── Flight controls (flaps, gear, speedbrake, reversers, AP) ──────
    def _update_systems(self):
        p    = self.perf
        alt  = self.alt
        spd  = self.speed

        # ─ Flaps ─────────────────────────────────────────────────────
        if self.phase in (Phase.GROUND_ROLL, Phase.ROTATION):
            flap_tgt = p.flap_to_deg
        elif self.phase == Phase.CLIMB:
            if alt < 400:
                flap_tgt = p.flap_to_deg
            elif alt < 1500:
                flap_tgt = p.flap_to_deg / 2.0
            else:
                flap_tgt = 0.0
        elif self.phase == Phase.CRUISE:
            flap_tgt = 0.0
        elif self.phase == Phase.DESCENT:
            if alt > 5000:
                flap_tgt = 0.0
            elif alt > 1500:
                flap_tgt = p.flap_to_deg
            else:
                flap_tgt = p.flap_land_deg * 0.75
        else:  # LANDING
            flap_tgt = p.flap_land_deg

        rate = 2.0 * self.dt                           # 2 °/s flap travel rate
        if self.flap_deg < flap_tgt:
            self.flap_deg = min(self.flap_deg + rate, flap_tgt)
        elif self.flap_deg > flap_tgt:
            self.flap_deg = max(self.flap_deg - rate, flap_tgt)

        # ─ Gear ──────────────────────────────────────────────────────
        if self.phase in (Phase.GROUND_ROLL, Phase.ROTATION, Phase.LANDING):
            self.gear = "DOWN"
            self._gear_transit_t = -1.0
        elif self.phase == Phase.CLIMB:
            if self.gear == "DOWN" and alt > 50:
                self.gear = "TRANSIT"
                self._gear_transit_t = self.time
            elif self.gear == "TRANSIT" and (self.time - self._gear_transit_t) > 15:
                self.gear = "UP"
        elif self.phase == Phase.CRUISE:
            self.gear = "UP"
        elif self.phase == Phase.DESCENT:
            if self.gear == "UP" and alt < 3000:
                self.gear = "TRANSIT"
                self._gear_transit_t = self.time
            elif self.gear == "TRANSIT" and (self.time - self._gear_transit_t) > 15:
                self.gear = "DOWN"

        # ─ Speedbrake / ground spoilers ───────────────────────────────
        self.speedbrake = (
            1.0 if self.phase == Phase.LANDING and alt <= 0 and spd > 30 else 0.0
        )

        # ─ Reverse thrust (transport jets / turboprops only) ──────────
        self.reverse_active = (
            p.is_transport
            and self.phase == Phase.LANDING
            and alt <= 0
            and spd > 40
        )

        # ─ Autopilot mode ─────────────────────────────────────────────
        if self.phase in (Phase.GROUND_ROLL, Phase.ROTATION):
            self.autopilot, self.ap_mode = False, "OFF"
        elif self.phase == Phase.CLIMB and alt < 500:
            self.autopilot, self.ap_mode = False, "TOGA"
        elif self.phase == Phase.CLIMB:
            self.autopilot, self.ap_mode = True,  "CLB"
        elif self.phase == Phase.CRUISE:
            self.autopilot, self.ap_mode = True,  "CRZ"
        elif self.phase == Phase.DESCENT and alt > 2000:
            self.autopilot, self.ap_mode = True,  "DES"
        elif self.phase == Phase.DESCENT:
            self.autopilot, self.ap_mode = False, "APP"
        else:  # LANDING
            self.autopilot, self.ap_mode = False, "FLARE" if alt > 0 else "ROLLOUT"

    # ── Heading / bank dynamics ───────────────────────────────────────
    def _turn_dynamics(self):
        if self.phase in (Phase.GROUND_ROLL, Phase.ROTATION, Phase.LANDING):
            self.roll = 0.0
            return
        if self.phase == Phase.CLIMB and self.alt < 1500:
            self.roll = 0.0
            return

        error = (self.target_heading - self.heading + 540) % 360 - 180

        if self.phase == Phase.CLIMB and not self._initial_turn_done:
            turn_rate = clamp(error * 0.15, -3.5, 3.5)
            self.roll  = clamp(turn_rate * 8, -30, 30)
            if abs(error) < 2:
                self._initial_turn_done = True
        else:
            turn_rate = clamp(error * 0.04, -1.5, 1.5)
            self.roll  = clamp(turn_rate * 7, -15, 15)

        self.heading = (self.heading + turn_rate * self.dt) % 360

    # ── Fuel burn ─────────────────────────────────────────────────────
    def _fuel_burn(self):
        mult = self._phase_fuel_mult()
        burn = (self._fuel_rate_cruise * mult + self.fuel_leak_rate) * self.dt
        self.fuel = max(0.0, self.fuel - burn)

    # ── Core physics ──────────────────────────────────────────────────
    def _physics(self):
        p = self.perf

        if self.phase == Phase.GROUND_ROLL:
            self.speed += self._ground_accel

        elif self.phase == Phase.ROTATION:
            self.pitch = min(self.pitch + p.rotation_rate_dps * self.dt, p.rotation_pitch_deg)
            self.alt  += 5.0

        elif self.phase == Phase.CLIMB:
            self.vs    = self._vs_climb
            self.alt  += self.vs * self.dt / 60.0
            self.speed += 0.02 * (p.climb_speed_kts - self.speed)
            self.pitch  = p.climb_pitch

        elif self.phase == Phase.CRUISE:
            self.vs    = 0.0
            self.alt   = self.cruise_alt
            self.speed += 0.01 * (self._cruise_speed - self.speed)
            self.pitch  = 2.0

        elif self.phase == Phase.DESCENT:
            self.vs    = self._vs_descent
            self.alt  += self.vs * self.dt / 60.0
            self.speed += 0.01 * (p.climb_speed_kts * 0.85 - self.speed)
            self.pitch  = -3.0

        elif self.phase == Phase.LANDING:
            if self.alt > 0:
                self.alt = max(0.0, self.alt - 50.0 * self.dt)
                self.vs  = -3000.0
            else:
                self.alt = 0.0
                self.vs  = 0.0
            extra_decel = self._ground_decel * 0.5 if self.reverse_active else 0.0
            self.speed  = max(0.0, self.speed - (self._ground_decel + extra_decel))
            self.pitch  = max(-2.0, self.pitch - 0.5 * self.dt)

        self._turn_dynamics()

        distance_km = self.speed * 1.852 * self.dt / 3600.0
        self.lat, self.lon = move(self.lat, self.lon, self.heading, distance_km)

        self._fuel_burn()
        self._pilot_inputs()
        self._update_systems()

    # ── State snapshot ────────────────────────────────────────────────
    def step(self):
        self.time += self.dt
        self._update_phase()
        self._physics()

        p   = self.perf
        alt = self.alt

        # ── Atmosphere ────────────────────────────────────────────────
        oat_c    = 15.0 - (alt / 1000.0) * 1.9812     # ISA lapse rate
        sigma    = max(0.001, (1 - alt * 6.87535e-6) ** 4.2561)  # density ratio
        sos_kts  = 661.5 * math.sqrt(max(0.01, (oat_c + 273.15) / 288.15))
        tas_kts  = self.speed / math.sqrt(sigma)
        mach     = tas_kts / sos_kts if sos_kts > 0 else 0.0
        tat_c    = oat_c * (1 + 0.2 * mach ** 2)       # total air temperature
        pres_alt = alt + (1013.25 - self.qnh_hpa) * 27.0
        dens_alt = alt + (15.0 - oat_c) * 120.0

        # ── Speeds ────────────────────────────────────────────────────
        gs = self._noise(self.speed, 0.5)

        # ── Wind components relative to track ─────────────────────────
        wind_rad       = math.radians(self.wind_dir)
        hdg_rad        = math.radians(self.heading)
        gnd_n          = tas_kts * math.cos(hdg_rad) + self.wind_spd_kts * math.cos(wind_rad)
        gnd_e          = tas_kts * math.sin(hdg_rad) + self.wind_spd_kts * math.sin(wind_rad)
        track_deg      = math.degrees(math.atan2(gnd_e, gnd_n)) % 360 if (gnd_n or gnd_e) else self.heading
        rel_wind       = math.radians(self.wind_dir - self.heading)
        headwind_kts   = self.wind_spd_kts * math.cos(rel_wind)   # + = headwind
        crosswind_kts  = self.wind_spd_kts * math.sin(rel_wind)   # + = from right

        # ── Attitude derived ──────────────────────────────────────────
        tas_fps   = tas_kts * 1.6878
        fpa_deg   = math.degrees(math.atan2(self.vs / 60.0, tas_fps)) if tas_fps > 5 else 0.0
        aoa_deg   = self.pitch - fpa_deg
        roll_rad  = math.radians(abs(self.roll))
        load_g    = (1.0 / math.cos(roll_rad)) if roll_rad < math.radians(85) else 5.0

        # ── N1, N2, EGT, fuel flow ────────────────────────────────────
        if self.reverse_active:
            n1_cmd = 65.0                              # reverse power N1
        else:
            n1_cmd = p.n1_idle_pct + (p.n1_toga_pct - p.n1_idle_pct) * self.throttle
        n2_cmd = 60.0 + self.throttle * 40.0          # N2 varies less than N1
        egt_cmd = 350.0 + self.throttle * 580.0       # EGT °C (idle ~350, TOGA ~930)

        n1_pct  = [round(self._noise(n1_cmd, 0.4), 1) for _ in range(p.engine_count)]
        n2_pct  = [round(self._noise(n2_cmd, 0.3), 1) for _ in range(p.engine_count)]
        egt_c   = [round(self._noise(egt_cmd, 5.0), 0) for _ in range(p.engine_count)]

        mult              = self._phase_fuel_mult()
        burn_pct_per_sec  = self._fuel_rate_cruise * mult
        burn_lbs_per_sec  = burn_pct_per_sec * p.fuel_capacity_lbs / 100.0
        ff_per_eng_pph    = burn_lbs_per_sec * 3600.0 / p.engine_count
        fuel_flow_pph     = [round(ff_per_eng_pph, 0) for _ in range(p.engine_count)]

        # ── Fuel / weight ─────────────────────────────────────────────
        fuel_lbs         = self.fuel / 100.0 * p.fuel_capacity_lbs
        fuel_burned_lbs  = (100.0 - self.fuel) / 100.0 * p.fuel_capacity_lbs
        gross_weight_lbs = p.tow_lbs - fuel_burned_lbs

        # ── Control surfaces (simplified but directionally realistic) ──
        trim_deg     = self.trim * 10.0
        elevator_deg = clamp(trim_deg * 1.5, -25.0, 25.0) + self._noise(0, 0.4)
        aileron_deg  = clamp(self.roll * 0.35, -20.0, 20.0) + self._noise(0, 0.3)
        rudder_deg   = clamp(crosswind_kts * 0.4 + aileron_deg * 0.08, -25.0, 25.0) + self._noise(0, 0.4)

        # ── Brakes ───────────────────────────────────────────────────
        if self.phase == Phase.LANDING and alt <= 0:
            if self.speed > 80:
                brake_pct = 60.0
            elif self.speed > 40:
                brake_pct = 85.0
            elif self.speed > 15:
                brake_pct = 40.0
            else:
                brake_pct = 0.0
        else:
            brake_pct = 0.0

        # ── Pressurization ────────────────────────────────────────────
        if alt < 10000:
            cabin_alt_ft = 0.0
        elif alt < p.cruise_alt_ft:
            cabin_alt_ft = (alt - 10000) / (p.cruise_alt_ft - 10000) * 7000.0
        else:
            cabin_alt_ft = 7000.0
        cabin_alt_ft = max(0.0, cabin_alt_ft)

        pres_inside  = 14.696 * (1 - cabin_alt_ft * 6.87535e-6) ** 5.2561
        pres_outside = 14.696 * (1 - alt * 6.87535e-6) ** 5.2561
        cabin_diff_psi = max(0.0, pres_inside - pres_outside)

        # ── Anti-ice ─────────────────────────────────────────────────
        anti_ice = (oat_c < 5.0) and (alt > 5000)

        # ── Build state dict ─────────────────────────────────────────
        state = {
            # ── Identity ────────────────────────────────────────────
            "time":  round(self.time, 3),
            "phase": self.phase.name,

            # ── Position ────────────────────────────────────────────
            "lat": round(self.lat, 6),
            "lon": round(self.lon, 6),

            # ── Speeds ──────────────────────────────────────────────
            "groundspeed_kts": round(gs, 1),
            "tas_kts":         round(tas_kts, 1),
            "cas_kts":         round(self.speed, 1),     # CAS ≈ IAS for this model
            "mach":            round(mach, 3),

            # ── Captain ADC ─────────────────────────────────────────
            "ias_captain":     round(self._noise(self.speed, 1.0), 1),
            "alt_captain":     round(self._noise(alt, 5.0)),
            "vs_captain":      round(self._noise(self.vs, 50.0)),

            # ── Captain AHRS ─────────────────────────────────────────
            "pitch_captain":   round(self._noise(self.pitch, 0.2), 2),
            "roll_captain":    round(self._noise(self.roll, 0.2), 2),
            "heading_captain": round(self._noise(self.heading, 0.5), 1),

            # ── FO ADC ──────────────────────────────────────────────
            "ias_fo":          round(self._noise(self.speed, 1.0), 1),
            "alt_fo":          round(self._noise(alt, 5.0)),
            "vs_fo":           round(self._noise(self.vs, 50.0)),

            # ── FO AHRS ─────────────────────────────────────────────
            "pitch_fo":        round(self._noise(self.pitch, 0.2), 2),
            "roll_fo":         round(self._noise(self.roll, 0.2), 2),
            "heading_fo":      round(self._noise(self.heading, 0.5), 1),

            # ── True values (ML ground truth) ────────────────────────
            "pitch_deg":       round(self.pitch, 2),
            "roll_deg":        round(self.roll, 2),
            "heading_deg":     round(self.heading, 1),
            "aoa_deg":         round(aoa_deg, 2),
            "fpa_deg":         round(fpa_deg, 2),
            "load_factor_g":   round(load_g, 3),

            # ── Navigation ──────────────────────────────────────────
            "track_deg":       round(track_deg, 1),

            # ── Atmosphere ──────────────────────────────────────────
            "oat_c":           round(oat_c, 1),
            "tat_c":           round(tat_c, 1),
            "qnh_hpa":         self.qnh_hpa,
            "pressure_alt_ft": round(pres_alt),
            "density_alt_ft":  round(dens_alt),

            # ── Wind ────────────────────────────────────────────────
            "wind_dir_deg":    self.wind_dir,
            "wind_spd_kts":    self.wind_spd_kts,
            "headwind_kts":    round(headwind_kts, 1),
            "crosswind_kts":   round(crosswind_kts, 1),

            # ── Engines ─────────────────────────────────────────────
            "throttle_pct":    round(self.throttle * 100, 1),
            "n1_pct":          n1_pct,
            "n2_pct":          n2_pct,
            "egt_c":           egt_c,
            "fuel_flow_pph":   fuel_flow_pph,        # per engine, lbs/hr
            "reverse_active":  self.reverse_active,
            "toga_active":     self.toga,

            # ── Fuel / weight ────────────────────────────────────────
            "fuel_pct":        round(self.fuel, 2),
            "fuel_lbs":        round(fuel_lbs),
            "gross_weight_lbs": round(gross_weight_lbs),

            # ── Flight controls ──────────────────────────────────────
            "flap_deg":        round(self.flap_deg, 1),
            "gear":            self.gear,
            "speedbrake":      round(self.speedbrake, 2),
            "trim_deg":        round(trim_deg, 2),

            # ── Control surfaces ─────────────────────────────────────
            "elevator_deg":    round(elevator_deg, 2),
            "aileron_deg":     round(aileron_deg, 2),
            "rudder_deg":      round(rudder_deg, 2),

            # ── Brakes ──────────────────────────────────────────────
            "brake_pct":       round(brake_pct, 1),

            # ── Pressurization ───────────────────────────────────────
            "cabin_alt_ft":    round(cabin_alt_ft),
            "cabin_diff_psi":  round(cabin_diff_psi, 2),

            # ── Autopilot ────────────────────────────────────────────
            "autopilot":       self.autopilot,
            "ap_mode":         self.ap_mode,

            # ── Anti-ice ────────────────────────────────────────────
            "anti_ice":        anti_ice,

            # ── V-speeds ─────────────────────────────────────────────
            "v1_kts":          p.v1_kts,
            "vr_kts":          p.vr_kts,
            "v2_kts":          p.v2_kts,
        }

        if self.emergency_fn:
            self.emergency_fn(state, self)

        return state


# ─────────────────────────────────────────────
# Standalone runner (not used by main.py)
# ─────────────────────────────────────────────
async def run_simulation(perf, airport_csv):
    gen        = FlightGenerator(perf, airport_csv)
    start_time = int(time.time())
    gen.meta["start_time"] = start_time

    part, last_rotate = 1, 0
    fname = f"flightData_{perf.model}_{start_time}_part{part}.jsonl"
    f = open(fname, "w")

    while gen.phase != Phase.COMPLETE:
        state = gen.step()
        f.write(json.dumps(state) + "\n")
        f.flush()
        print(state)

        if gen.time - last_rotate > 300:
            f.close()
            part += 1
            last_rotate = gen.time
            fname = f"flightData_{perf.model}_{start_time}_part{part}.jsonl"
            f = open(fname, "w")

        await asyncio.sleep(gen.dt)

    f.close()
    print("Flight complete")
    merged = merge_flight_logs(perf.model, start_time, part)
    build_final_output(perf.model, start_time, merged, gen.meta)
