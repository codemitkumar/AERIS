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
    x = math.cos(math.radians(lat1))*math.sin(math.radians(lat2)) - \
        math.sin(math.radians(lat1))*math.cos(math.radians(lat2))*math.cos(dlon)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


# ─────────────────────────────────────────────
# Merge Logs
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
                print(f"⚠️ Missing {part_file}, skipping")

    if cleanup:
        for p in range(1, parts + 1):
            part_file = f"flightData_{model}_{start_time}_part{p}.jsonl"
            if os.path.exists(part_file):
                os.remove(part_file)

    print(f"✅ Merged: {final_name}")
    return final_name


def build_final_output(model, start_time, jsonl_file, meta):
    final_json = f"flightData_{model}_{start_time}.json"

    simulation = []
    with open(jsonl_file, "r") as f:
        for line in f:
            simulation.append(json.loads(line))

    meta["duration"] = simulation[-1]["time"] if simulation else 0
    meta["remaining_fuel"] = simulation[-1]["fuel_percent"] if simulation else 0

    final = {
        "meta": meta,
        "simulation": simulation
    }

    with open(final_json, "w") as f:
        json.dump(final, f)

    print(f"✅ Final JSON created: {final_json}")


# ─────────────────────────────────────────────
# Phase
# ─────────────────────────────────────────────
class Phase(Enum):
    GROUND_ROLL = auto()
    ROTATION = auto()
    CLIMB = auto()
    CRUISE = auto()
    DESCENT = auto()
    COMPLETE = auto()


# ─────────────────────────────────────────────
# Generator
# ─────────────────────────────────────────────
class FlightGenerator:

    def __init__(self, perf, airport_csv, dt=1/30, emergency_fn=None):
        self.perf = perf
        self.dt = dt
        self.emergency_fn = emergency_fn

        self.time = 0
        self.phase = Phase.GROUND_ROLL

        self.alt = 0
        self.speed = 0
        self.pitch = 0
        self.roll = 0
        self.heading = 0  # overwritten after departure_heading is set below
        self.vs = 0

        self.initial_fuel = 100.0
        self.fuel = self.initial_fuel
        self.fuel_leak_rate = 0.0

        # ── Derived from perf ──────────────────────────────────────────
        t2w = perf.thrust_total_lbs / perf.tow_lbs

        # Ground roll: kts per step from thrust-to-weight, clamped for stability
        self._ground_accel = clamp(t2w * 3.0, 0.3, 1.5)

        # Climb VS: speed × sin(pitch) × unit conversion, capped at 2500 fpm
        vs_raw = perf.climb_speed_kts * math.sin(math.radians(perf.climb_pitch)) * 101.269
        self._vs_climb   = clamp(vs_raw, 300.0, 2500.0)
        self._vs_descent = -2000.0 if perf.is_transport else -800.0

        # Cruise IAS target
        if perf.is_transport:
            self._cruise_speed = 280.0
        else:
            self._cruise_speed = clamp(perf.climb_speed_kts * 1.1, 60.0, 130.0)

        # Fuel burn rate at cruise in %/sec of simulation time (scales with T/W)
        self._fuel_rate_cruise = t2w * 0.12

        self.airports = self._load_airports(airport_csv)
        self.origin, self.dest = random.sample(self.airports, 2)

        self.lat = self.origin["lat"]
        self.lon = self.origin["lon"]

        self.target_heading = bearing(
            self.lat, self.lon,
            self.dest["lat"], self.dest["lon"]
        )

        # Runway heading: random direction, independent of destination
        self.departure_heading = random.choice(range(0, 360, 10)) * 1.0
        self._initial_turn_done = False
        self.heading = self.departure_heading

        self.cruise_alt = min(perf.cruise_alt_ft, 30000)
        if perf.model == "c172p":
            self.cruise_alt = random.randint(5000, 10000)

        # Logging cadence: time-based interval + event triggers
        self._log_interval = 60.0 if perf.model == "c172p" else 180.0
        self._last_log_time = -999.0        # force first entry immediately
        self._prev_log_phase = None
        self._prev_log_throttle = -1.0      # sentinel: differs from any real throttle
        self._prev_log_heading = self.heading
        self._prev_log_vs_sign = 0

        self.meta = {
            "model": perf.model,
            "origin": self.origin,
            "destination": self.dest,
            "initial_fuel": self.initial_fuel
        }

    def _load_airports(self, path):
        with open(path, encoding="utf-8", errors="replace") as f:
            return [
                {"lat": float(r["latitude_deg"]), "lon": float(r["longitude_deg"])}
                for r in csv.DictReader(f)
                if r.get("latitude_deg") and r.get("longitude_deg")
            ]

    def _noise(self, v, n):
        return v + random.uniform(-n, n)

    def should_log(self) -> bool:
        """Return True when this tick should be persisted to the log file.

        Triggers:
          - phase transition
          - throttle change > 5 %
          - heading change > 15 °
          - vertical-speed sign change (climb ↔ level ↔ descent)
          - elapsed time since last log ≥ interval
        """
        throttle = getattr(self, "throttle", 0.0)
        vs_sign = 1 if self.vs > 50 else (-1 if self.vs < -50 else 0)
        heading_delta = abs((self.heading - self._prev_log_heading + 180) % 360 - 180)

        triggered = (
            self.phase != self._prev_log_phase
            or abs(throttle - self._prev_log_throttle) > 0.05
            or heading_delta > 15
            or vs_sign != self._prev_log_vs_sign
            or (self.time - self._last_log_time) >= self._log_interval
        )

        if triggered:
            self._last_log_time = self.time
            self._prev_log_phase = self.phase
            self._prev_log_throttle = throttle
            self._prev_log_heading = self.heading
            self._prev_log_vs_sign = vs_sign

        return triggered

    def _update_phase(self):
        if self.phase == Phase.GROUND_ROLL and self.speed >= self.perf.vr_kts:
            self.phase = Phase.ROTATION
        elif self.phase == Phase.ROTATION and self.alt > 50:
            self.phase = Phase.CLIMB
        elif self.phase == Phase.CLIMB and self.alt >= self.cruise_alt:
            self.phase = Phase.CRUISE
        elif self.phase == Phase.CRUISE and self.fuel < 30:
            self.phase = Phase.DESCENT
        elif self.phase == Phase.DESCENT and self.alt <= 100:
            self.phase = Phase.COMPLETE

    def _turn_dynamics(self):
        # Ground roll and rotation: hold runway heading, wings level
        if self.phase in [Phase.GROUND_ROLL, Phase.ROTATION]:
            self.roll = 0
            return

        # Stay on departure heading until safely climbing (1500 ft AGL)
        if self.phase == Phase.CLIMB and self.alt < 1500:
            self.roll = 0
            return

        error = (self.target_heading - self.heading + 540) % 360 - 180

        # Big banked turn toward destination after initial climb
        if self.phase == Phase.CLIMB and not self._initial_turn_done:
            turn_rate = clamp(error * 0.15, -3.5, 3.5)
            self.roll = clamp(turn_rate * 8, -30, 30)
            if abs(error) < 2:
                self._initial_turn_done = True
        else:
            # Minor heading corrections in cruise/descent
            turn_rate = clamp(error * 0.04, -1.5, 1.5)
            self.roll = clamp(turn_rate * 7, -15, 15)

        self.heading = (self.heading + turn_rate * self.dt) % 360

    def _fuel_burn(self):
        phase_mult = {
            Phase.GROUND_ROLL: 1.2,
            Phase.ROTATION:    1.3,
            Phase.CLIMB:       1.5,
            Phase.CRUISE:      1.0,
            Phase.DESCENT:     0.6,
        }.get(self.phase, 1.0)

        burn = (self._fuel_rate_cruise * phase_mult + self.fuel_leak_rate) * self.dt
        self.fuel = max(0.0, self.fuel - burn)

    def _pilot_inputs(self):
        if self.phase in [Phase.GROUND_ROLL, Phase.ROTATION, Phase.CLIMB]:
            self.throttle = 1.0
        else:
            self.throttle = 0.7

        self.toga = self.phase in [Phase.GROUND_ROLL, Phase.ROTATION]
        self.trim = clamp(self.pitch * 0.1, -1, 1)
        self.engines = [self.throttle] * self.perf.engine_count

    def _physics(self):
        p = self.perf

        if self.phase == Phase.GROUND_ROLL:
            self.speed += self._ground_accel

        elif self.phase == Phase.ROTATION:
            self.pitch = min(self.pitch + p.rotation_rate_dps * self.dt,
                             p.rotation_pitch_deg)
            self.alt += 5

        elif self.phase == Phase.CLIMB:
            self.vs    = self._vs_climb
            self.alt  += self.vs * self.dt / 60
            self.speed += 0.02 * (p.climb_speed_kts - self.speed)
            self.pitch  = p.climb_pitch

        elif self.phase == Phase.CRUISE:
            self.vs    = 0
            self.alt   = self.cruise_alt
            self.speed += 0.01 * (self._cruise_speed - self.speed)
            self.pitch  = 2

        elif self.phase == Phase.DESCENT:
            self.vs    = self._vs_descent
            self.alt  += self.vs * self.dt / 60
            self.speed += 0.01 * (p.climb_speed_kts * 0.85 - self.speed)
            self.pitch  = -3

        self._turn_dynamics()

        distance_km = self.speed * 1.852 * self.dt / 3600
        self.lat, self.lon = move(self.lat, self.lon, self.heading, distance_km)

        self._fuel_burn()
        self._pilot_inputs()

    def step(self):
        self.time += self.dt
        self._update_phase()
        self._physics()

        state = {
            "time": round(self.time, 3),
            "phase": self.phase.name,
            "lat": self.lat,
            "lon": self.lon,
            "groundspeed_kts": self._noise(self.speed, 0.5),

            "pitch_captain": self._noise(self.pitch, 0.2),
            "roll_captain": self._noise(self.roll, 0.2),
            "heading_captain": self._noise(self.heading, 0.5),
            "ias_captain": self._noise(self.speed, 1),
            "alt_captain": self._noise(self.alt, 5),
            "vs_captain": self._noise(self.vs, 50),

            "pitch_fo": self._noise(self.pitch, 0.2),
            "roll_fo": self._noise(self.roll, 0.2),
            "heading_fo": self._noise(self.heading, 0.5),
            "ias_fo": self._noise(self.speed, 1),
            "alt_fo": self._noise(self.alt, 5),
            "vs_fo": self._noise(self.vs, 50),

            "fuel_percent": self.fuel,

            "throttle": self.throttle,
            "toga_active": self.toga,
            "trim": self.trim,
            "engines": self.engines,

            "v1_kts": self.perf.v1_kts,
            "vr_kts": self.perf.vr_kts,
            "v2_kts": self.perf.v2_kts,
        }

        if self.emergency_fn:
            self.emergency_fn(state, self)

        return state


# ─────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────
async def run_simulation(perf, airport_csv):

    gen = FlightGenerator(perf, airport_csv)

    start_time = int(time.time())
    gen.meta["start_time"] = start_time

    part = 1
    last_rotate = 0

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
    print("✅ Flight complete")

    merged = merge_flight_logs(perf.model, start_time, part)
    build_final_output(perf.model, start_time, merged, gen.meta)