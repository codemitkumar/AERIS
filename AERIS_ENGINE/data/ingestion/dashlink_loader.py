"""NASA DASHlink FDR replay.

Replays a real flight recorder file through the same DataBus the synthetic
generator feeds. Each .mat holds ~186 channels recorded at 0.25-16 Hz; replay
ticks at the fastest rate and holds the last sample of slower channels, so
nothing is interpolated into existence.

Discrete channels (GPWS, SHKR, FIRE_n, ...) are passed through under
fdr_discretes but never mapped to alert semantics - their polarity is
inconsistent and undocumented. GPWS and WSHR sit at 1 for an entire normal
flight while FIRE_n sits at 0, and SHKR alternates 50/50.
"""

import math
import os

from data.ingestion.flight_common import Phase, haversine_nm

try:
    from scipy.io import loadmat
    _SCIPY = True
except ImportError:
    _SCIPY = False


# One channel can feed several AERIS keys - a single recorded CAS stands in
# for both crew airspeed indicators, which FDRs rarely record separately.
_DIRECT = {
    "ALT":   ("pressure_alt_ft",),
    "BAL1":  ("alt_captain",),
    "BAL2":  ("alt_fo",),
    "CAS":   ("cas_kts", "ias_captain", "ias_fo"),
    "TAS":   ("tas_kts",),
    "GS":    ("groundspeed_kts",),
    "MACH":  ("mach",),
    "IVV":   ("vs_captain", "vs_fo"),
    "ALTR":  ("altitude_rate_fpm",),
    "PTCH":  ("pitch_deg", "pitch_captain", "pitch_fo"),
    "ROLL":  ("roll_deg", "roll_captain", "roll_fo"),
    "TH":    ("heading_deg", "heading_captain", "heading_fo"),
    "MH":    ("magnetic_heading_deg",),
    "TRK":   ("track_deg",),
    "RALT":  ("radio_alt_ft",),
    "LATP":  ("lat",),
    "LONP":  ("lon",),
    "SAT":   ("oat_c",),
    "TAT":   ("tat_c",),
    "WS":    ("wind_spd_kts",),
    "WD":    ("wind_dir_deg",),
    "VRTG":  ("load_factor_g",),
    "LATG":  ("lateral_g",),
    "LONG":  ("longitudinal_g",),
    "AOAC":  ("aoa_deg", "aoa_corrected_deg"),
    "AOAI":  ("aoa_indicated_deg",),
    "AOA1":  ("aoa_1_deg",),
    "AOA2":  ("aoa_2_deg",),
    "DA":    ("drift_angle_deg",),
    "PI":    ("impact_press_mb",),
    "PS":    ("static_press_inhg",),
    "PSA":   ("static_press_mb",),
    "PT":    ("total_press_mb",),
    "GLS":   ("glideslope_ddm",),
    "LOC":   ("localizer_ddm",),
    "ALTS":  ("selected_alt_ft",),
    "CASS":  ("selected_ias_kts",),
    "HDGS":  ("selected_heading_deg",),
    "VSPS":  ("selected_vs_fpm",),
    "MNS":   ("selected_mach",),
    "N1C":   ("n1_command_pct",),
    "N1T":   ("n1_target_pct",),
    "CASM":  ("max_allowable_ias_kts",),
    "RUDD":  ("rudder_deg",),
    "PTRM":  ("trim_deg",),
    "ABRK":  ("airbrake_deg",),
    "FLAP":  ("flap_counts",),
    "CCPC":  ("control_column_capt",),
    "CCPF":  ("control_column_fo",),
    "CWPC":  ("control_wheel_capt",),
    "CWPF":  ("control_wheel_fo",),
    "RUDP":  ("rudder_pedal_pos",),
}

# Per-engine channels -> list-valued AERIS keys.
_PER_ENGINE = {
    "N1":   "n1_pct",
    "N2":   "n2_pct",
    "EGT":  "egt_c",
    "FF":   "fuel_flow_pph",
    "VIB":  "eng_vibration_ips",
    "OIP":  "eng_oil_press_psi",
    "OIT":  "eng_oil_temp_c",
    "PLA":  "power_lever_angle_deg",
}

# Reference only, see module docstring.
_DISCRETES = (
    "FIRE_1", "FIRE_2", "FIRE_3", "FIRE_4", "SHKR", "GPWS", "WSHR", "SMOK",
    "SMKB", "MW", "TOCW", "PUSH", "TCAS", "MRK", "EAI", "TAI", "WAI_1",
    "WAI_2", "HYDG", "HYDY", "PACK", "LGDN", "LGUP", "WOW", "MSQT_1",
    "MSQT_2", "NSQT", "PH", "APFD", "ATEN", "A_T", "SPLG", "SPLY",
)

_ICAO_FINAL_RESERVE_MIN = 30.0   # ICAO Annex 6 final reserve, minutes of holding

# Offset bookkeeping name -> the attribute an injector actually writes.
_OFFSET_ATTR = {
    "alt": "alt", "speed": "speed", "vs": "vs",
    "fuel_left": "fuel_left_lbs", "fuel_right": "fuel_right_lbs",
    "fuel_center": "fuel_center_lbs",
}

# Recorders emit fixed sentinels for dropped samples rather than gaps: VRTG
# parks at exactly -3.375 for 2.8% of a normal flight, oil temp at -390.
# Read as real those produced 1,635 phantom negative-G alerts on one flight.
_VALID_RANGE = {
    "VRTG":  (-2.0, 4.0),      # g
    "LATG":  (-1.5, 1.5),
    "LONG":  (-1.5, 1.5),
    "OIT_1": (-60.0, 250.0),   # deg C
    "OIT_2": (-60.0, 250.0),
    "OIT_3": (-60.0, 250.0),
    "OIT_4": (-60.0, 250.0),
    "OIP_1": (0.0, 200.0),     # psi
    "OIP_2": (0.0, 200.0),
    "OIP_3": (0.0, 200.0),
    "OIP_4": (0.0, 200.0),
    "SAT":   (-90.0, 60.0),    # deg C
    "TAT":   (-90.0, 80.0),
    "RALT":  (-20.0, 60000.0), # ft
    "CAS":   (0.0, 500.0),     # kt
    "TAS":   (0.0, 700.0),
    "GS":    (0.0, 700.0),
    "MACH":  (0.0, 1.2),
    "PTCH":  (-40.0, 40.0),    # deg
    "ROLL":  (-90.0, 90.0),
    "IVV":   (-12000.0, 12000.0),  # fpm
    "ALTR":  (-12000.0, 12000.0),
}


def _scalar(arr, default=None):
    try:
        flat = arr.ravel()
        return flat[0] if flat.size else default
    except Exception:
        return default


class DashlinkFlight:
    """Parsed DASHlink `.mat`: channels, their rates, and derived metadata."""

    def __init__(self, path: str):
        if not _SCIPY:
            raise RuntimeError(
                "scipy is required to read DASHlink .mat files — pip install scipy"
            )
        self.path = path
        self.name = os.path.basename(path)
        self.channels: dict[str, dict] = {}

        raw = loadmat(path, struct_as_record=False, squeeze_me=False)
        for key, val in raw.items():
            if key.startswith("__"):
                continue
            try:
                rec = val[0, 0]
                data = rec.data.ravel().astype(float)
                rate = float(rec.Rate.ravel()[0])
                units = _scalar(rec.Units, "")
                desc = _scalar(rec.Description, "")
            except Exception:
                continue
            if data.size == 0 or rate <= 0:
                continue
            self.channels[key] = {
                "data": data,
                "rate": rate,
                "units": str(units).strip(),
                "desc": str(desc).strip(),
            }

        if "ALT" not in self.channels:
            raise ValueError(f"{self.name}: no ALT channel — not a usable FDR file")

        # Some channels are wired but never populated - FQTY_3 reads a flat
        # zero all flight. Counting those invents fuel imbalances.
        self.dead_channels = {
            k for k, c in self.channels.items()
            if len(c["data"]) and float(c["data"].min()) == float(c["data"].max()) == 0.0
        }

        self.base_rate = max(c["rate"] for c in self.channels.values())
        self.duration_s = max(
            len(c["data"]) / c["rate"] for c in self.channels.values()
        )
        self.n_ticks = int(self.duration_s * self.base_rate)
        self.engine_count = sum(
            1 for i in range(1, 9)
            if f"N1_{i}" in self.channels and f"N1_{i}" not in self.dead_channels
        ) or 1
        self.phases = self._segment_phases()

    def live(self, channel: str) -> bool:
        """True if the channel exists and carries real readings."""
        return channel in self.channels and channel not in self.dead_channels

    def _segment_phases(self) -> list:
        """Label the flight's phases up front.

        A replay knows the whole flight, so phases come from the actual
        altitude profile — where the peak is, where climb and descent start —
        rather than from instantaneous vertical speed, which mislabels every
        level-off in a step climb as cruise.
        """
        alt_c = self.channels["ALT"]
        alt, rate = alt_c["data"], alt_c["rate"]
        n = len(alt)
        if n < 10:
            return [(0.0, Phase.CRUISE)]

        peak = float(alt.max())
        ground = float(alt.min())
        band = max(200.0, (peak - ground) * 0.05)

        airborne = alt > ground + band
        idx = [i for i, a in enumerate(airborne) if a]
        if not idx:
            return [(0.0, Phase.GROUND_ROLL)]
        first, last = idx[0], idx[-1]

        # Cruise = sustained flight within 5% of the peak altitude.
        near_peak = [i for i in range(first, last + 1) if alt[i] > peak - band]
        toc = near_peak[0] if near_peak else first
        tod = near_peak[-1] if near_peak else last

        marks = [
            (0.0, Phase.GROUND_ROLL),
            (first / rate, Phase.CLIMB),
            (toc / rate, Phase.CRUISE),
            (tod / rate, Phase.DESCENT),
            (last / rate, Phase.LANDING),
        ]
        if last < n - 1:
            marks.append(((last + 1) / rate, Phase.COMPLETE))
        # Keep strictly increasing, drop degenerate segments.
        out = []
        for t, p in marks:
            if not out or t > out[-1][0]:
                out.append((t, p))
        return out

    def phase_at(self, t: float) -> Phase:
        current = self.phases[0][1]
        for start, phase in self.phases:
            if t >= start:
                current = phase
            else:
                break
        return current

    def at(self, channel: str, t: float, default=0.0):
        """Value of `channel` at time `t` seconds — the most recent real
        sample, never an interpolated one.

        Samples outside the channel's plausible range are recorder sentinels,
        not readings; the last good value is held instead (see _VALID_RANGE).
        """
        c = self.channels.get(channel)
        if not c:
            return default
        idx = int(t * c["rate"])
        if idx < 0:
            return default
        if idx >= len(c["data"]):
            idx = len(c["data"]) - 1
        v = c["data"][idx]
        if math.isnan(v):
            return c.get("_last_good", default)

        rng = _VALID_RANGE.get(channel)
        if rng and not (rng[0] <= v <= rng[1]):
            c["_invalid"] = c.get("_invalid", 0) + 1
            return c.get("_last_good", default)

        c["_last_good"] = float(v)
        return float(v)

    def invalid_counts(self) -> dict:
        """Samples rejected as sentinels, per channel."""
        return {
            k: c["_invalid"] for k, c in self.channels.items() if c.get("_invalid")
        }


class DashlinkReplay:
    """Replays a DashlinkFlight with the surface `main.py` and the emergency
    injectors expect from FlightGenerator.

    Only the attributes those callers actually touch are provided — this is a
    replay source, not a physics model, so anything that would require
    simulating forward is absent by design.
    """

    def __init__(self, flight: DashlinkFlight, perf, airports=None,
                 emergency_fn=None, log_interval_s: float = 60.0):
        self.flight = flight
        self.perf = perf
        self.emergency_fn = emergency_fn
        self.region = "US"
        self.airports = airports or []

        self.dt = 1.0 / flight.base_rate
        self.time = 0.0
        self.phase = Phase.GROUND_ROLL
        self._log_interval = log_interval_s
        self._last_log_t = -1e9

        # Fuel bookkeeping the injectors mutate (see emergencyInjector shim).
        self.fuel_total_lbs = self._fuel_total_at(0.0)
        self._initial_fuel_lbs = max(1.0, self.fuel_total_lbs)
        self.fuel_used_lbs = 0.0
        self.fuel = 100.0
        self.fuel_left_lbs = self.fuel_total_lbs / 2.0
        self.fuel_right_lbs = self.fuel_total_lbs / 2.0
        self.fuel_center_lbs = 0.0
        self.fuel_flow_total_lbs_hr = 0.0

        # An injector writes gen.alt and expects the next tick to build on it.
        # A replay can't do that — the next tick comes off the recording — so
        # what an injector writes is kept as a standing offset and re-applied
        # every tick. A descent injector subtracting 10 ft per tick therefore
        # compounds the way it does against the generator's physics.
        self._offsets = {
            "alt": 0.0, "speed": 0.0, "vs": 0.0,
            "fuel_left": 0.0, "fuel_right": 0.0, "fuel_center": 0.0,
        }
        self._fuel_base = (0.0, 0.0, 0.0)

        # Position/attitude mirrors, so injectors that write gen.alt/gen.speed
        # have somewhere to write. Applied as offsets — see _offsets.
        self.alt = flight.at("ALT", 0.0)
        self.speed = flight.at("CAS", 0.0)
        self.vs = flight.at("IVV", 0.0)
        self.lat = flight.at("LATP", 0.0)
        self.lon = flight.at("LONP", 0.0)
        self.heading = flight.at("TH", 0.0)

        self.origin, self.dest = self._endpoints()
        self.route_distance_nm = (
            haversine_nm(self.origin["lat"], self.origin["lon"],
                         self.dest["lat"], self.dest["lon"])
            if self.origin and self.dest else 0.0
        )
        self.departure_heading = flight.at("TH", 0.0)
        self.landing_heading = flight.at("TH", flight.duration_s - 1)
        self._cruise_speed = max(1.0, self._median_cruise_speed())
        self.alternates = []

    # ── derived metadata ────────────────────────────────────────────────

    def _fuel_total_at(self, t: float) -> float:
        total = 0.0
        for i in range(1, 9):
            key = f"FQTY_{i}"
            if key in self.flight.channels:
                total += self.flight.at(key, t)
        return total

    def _fuel_flow_total_at(self, t: float) -> float:
        total = 0.0
        for i in range(1, 9):
            key = f"FF_{i}"
            if key in self.flight.channels:
                total += self.flight.at(key, t)
        return total

    def _median_cruise_speed(self) -> float:
        c = self.flight.channels.get("GS")
        if not c:
            return 1.0
        d = c["data"]
        d = d[d > 100]
        return float(sorted(d)[len(d) // 2]) if len(d) else 1.0

    def _nearest_airport(self, lat, lon):
        if not self.airports or lat == 0.0:
            return None
        best, best_d = None, 1e9
        for a in self.airports:
            alat, alon = a.get("lat"), a.get("lon")
            if alat is None or alon is None:
                continue
            d = haversine_nm(lat, lon, alat, alon)
            if d < best_d:
                best, best_d = a, d
        return best if best_d < 30.0 else None

    def _endpoints(self):
        """Origin/destination inferred from where the recorded track starts and
        ends — a real FDR carries no flight plan."""
        f = self.flight
        o = self._nearest_airport(f.at("LATP", 0.0), f.at("LONP", 0.0))
        d = self._nearest_airport(f.at("LATP", f.duration_s - 1),
                                  f.at("LONP", f.duration_s - 1))
        fallback = {"icao": "", "iata": "", "lat": 0.0, "lon": 0.0, "name": ""}
        return o or fallback, d or fallback

    # ── replay ──────────────────────────────────────────────────────────

    def step(self) -> dict:
        f = self.flight
        t = self.time

        state: dict = {
            "time": round(t, 3),
            "dt": self.dt,
            "sample_rate_hz": round(f.base_rate, 4),
            "source": "dashlink",
            "source_file": f.name,
        }

        for ch, keys in _DIRECT.items():
            if not f.live(ch):
                continue
            v = f.at(ch, t)
            for k in keys:
                state[k] = v

        for prefix, key in _PER_ENGINE.items():
            vals = [
                f.at(f"{prefix}_{i}", t)
                for i in range(1, f.engine_count + 1)
                if f.live(f"{prefix}_{i}")
            ]
            if vals:
                state[key] = vals

        state["fdr_discretes"] = {
            d: f.at(d, t) for d in _DISCRETES if d in f.channels
        }

        # ── fuel ────────────────────────────────────────────────────────
        tank_ids = [i for i in range(1, 9) if f.live(f"FQTY_{i}")]
        tanks = [f.at(f"FQTY_{i}", t) for i in tank_ids]
        recorded_total = sum(tanks)
        flow_total = self._fuel_flow_total_at(t)

        # Tanks are numbered outboard-to-outboard across the wing, so the
        # low-numbered half is the left wing. Dead channels are already gone,
        # which matters: counting a flat-zero tank as "right wing" fabricates
        # a standing imbalance for the whole flight.
        half = len(tanks) // 2 or 1
        base_left = sum(tanks[:half])
        base_right = sum(tanks[half:]) or base_left
        base_center = 0.0
        self._fuel_base = (base_left, base_right, base_center)

        self.fuel_left_lbs = max(0.0, base_left + self._offsets["fuel_left"])
        self.fuel_right_lbs = max(0.0, base_right + self._offsets["fuel_right"])
        self.fuel_center_lbs = max(0.0, base_center + self._offsets["fuel_center"])
        self.fuel_total_lbs = (self.fuel_left_lbs + self.fuel_right_lbs
                               + self.fuel_center_lbs)
        self.fuel_flow_total_lbs_hr = flow_total
        self.fuel_used_lbs = max(0.0, self._initial_fuel_lbs - self.fuel_total_lbs)
        self.fuel = (self.fuel_total_lbs / self._initial_fuel_lbs * 100.0
                     if self._initial_fuel_lbs > 0 else 0.0)

        state.update({
            "fuel_qty_tanks_lbs": [round(q, 1) for q in tanks],
            "fuel_total_lbs": round(self.fuel_total_lbs, 1),
            "fuel_used_lbs": round(self.fuel_used_lbs, 1),
            "fuel_left_lbs": round(self.fuel_left_lbs, 1),
            "fuel_right_lbs": round(self.fuel_right_lbs, 1),
            "fuel_center_lbs": round(self.fuel_center_lbs, 1),
            "fuel_pct": round(self.fuel, 2),
            "fuel_lbs": round(self.fuel_total_lbs),
            "fuel_flow_total_lbs_hr": round(flow_total, 1),
            "fuel_flow_eng1_lbs_hr": round(f.at("FF_1", t), 1),
            "fuel_flow_eng2_lbs_hr": round(f.at("FF_2", t), 1),
            # A real FDR has no flight plan; hold the ICAO final reserve
            # (30 min at the current burn) so GRACE has a real floor to
            # respect instead of defaulting to zero and over-recommending.
            "fuel_reserve_min_lbs": round(
                flow_total * (_ICAO_FINAL_RESERVE_MIN / 60.0), 1),
        })

        # ── phase / configuration ───────────────────────────────────────
        alt = state.get("pressure_alt_ft", 0.0)
        ra = state.get("radio_alt_ft", alt)
        cas = state.get("cas_kts", 0.0)
        vs = state.get("vs_captain", 0.0)
        on_ground = bool(f.at("WOW", t, 0.0)) if "WOW" in f.channels else ra < 10

        self.phase = f.phase_at(t)
        # What the recorder actually saw this tick, before any injection. The
        # offsets are measured against this, so they accumulate across ticks
        # instead of being recomputed from an already-offset value.
        recorded = {"alt": alt, "speed": cas, "vs": vs,
                    "fuel_left": self._fuel_base[0],
                    "fuel_right": self._fuel_base[1],
                    "fuel_center": self._fuel_base[2]}
        carry = dict(self._offsets)
        self.alt = alt + carry["alt"]
        self.speed = cas + carry["speed"]
        self.vs = vs + carry["vs"]
        self.lat = state.get("lat", self.lat)
        self.lon = state.get("lon", self.lon)
        self.heading = state.get("heading_deg", self.heading)

        gear_down = bool(f.at("LGDN", t, 0.0)) if "LGDN" in f.channels else on_ground
        state.update({
            "phase": self.phase.name,
            "weight_on_wheels": int(on_ground),
            "gear": "DOWN" if gear_down else "UP",
            "gear_down_locked": int(gear_down),
            "flap_deg": self._flap_deg(state.get("flap_counts")),
            "throttle_pct": self._throttle_pct(state.get("power_lever_angle_deg")),
            "origin_icao": self.origin.get("icao", ""),
            "destination_icao": self.dest.get("icao", ""),
            "dist_to_dest_nm": round(haversine_nm(
                self.lat, self.lon, self.dest.get("lat", self.lat),
                self.dest.get("lon", self.lon)), 1) if self.dest.get("lat") else 0.0,
        })

        # V-speeds and limits come from the performance profile, not the
        # recorder — the modules need them to have anything to compare against.
        p = self.perf
        state.update({
            "v1_kts": p.v1_kts, "vr_kts": p.vr_kts, "v2_kts": p.v2_kts,
            "vref_kts": p.vref_kts, "best_glide_kts": p.best_glide_kts,
            "qnh_hpa": 1013.25,
            "baro_setting_captain_hpa": 1013.25,
            "baro_setting_fo_hpa": 1013.25,
        })

        if self.emergency_fn:
            self.emergency_fn(state, self)
            self._absorb_injector_writes(state, recorded, carry)

        self.time += self.dt
        return state

    def _absorb_injector_writes(self, state: dict, recorded: dict,
                                carry: dict) -> None:
        """Make an injector's effect survive into the next tick.

        An injector writes both the state (its effect for this tick) and
        gen.alt / gen.speed / gen.fuel_* (so the generator's physics carries it
        forward). A replay has no physics to carry anything forward, so the
        attribute writes are turned into a standing offset instead.

        `carry` is what had accumulated before this tick; the injector's own
        state write covers this tick. Adding them gives the full effect without
        double-counting the current tick.
        """
        if carry["alt"]:
            for k in ("pressure_alt_ft", "alt_captain", "alt_fo", "radio_alt_ft"):
                if k in state:
                    state[k] = max(0.0, state[k] + carry["alt"])

        if carry["speed"]:
            for k in ("cas_kts", "ias_captain", "ias_fo", "tas_kts"):
                if k in state:
                    state[k] = max(0.0, state[k] + carry["speed"])

        if carry["vs"]:
            for k in ("vs_captain", "vs_fo"):
                if k in state:
                    state[k] += carry["vs"]

        for key, attr in _OFFSET_ATTR.items():
            self._offsets[key] = getattr(self, attr) - recorded[key]

        # The fuel injectors write their own state keys off gen.fuel_*, which
        # is already offset-correct — only the totals need recomputing.
        self.fuel_total_lbs = (self.fuel_left_lbs + self.fuel_right_lbs
                               + self.fuel_center_lbs)
        self.fuel_used_lbs = max(0.0, self._initial_fuel_lbs - self.fuel_total_lbs)
        self.fuel = (self.fuel_total_lbs / self._initial_fuel_lbs * 100.0
                     if self._initial_fuel_lbs > 0 else 0.0)
        state["fuel_total_lbs"] = round(self.fuel_total_lbs, 1)
        state["fuel_used_lbs"] = round(self.fuel_used_lbs, 1)
        state["fuel_lbs"] = round(self.fuel_total_lbs)
        state["fuel_pct"] = round(self.fuel, 2)

    def reset_offsets(self) -> None:
        """Snap back to the recording.

        Stopping an injector deliberately leaves its offset in place — an
        aircraft that has been pushed 2 000 ft down does not spring back when
        the fault clears. This is the explicit undo for that.
        """
        for key in self._offsets:
            self._offsets[key] = 0.0

    @staticmethod
    def _flap_deg(counts):
        """FDR records flap as raw counts; scale to degrees against the
        observed 113–3691 count range of the recorded fleet."""
        if counts is None:
            return 0.0
        return round(max(0.0, (counts - 113.0) / (3691.0 - 113.0) * 40.0), 1)

    @staticmethod
    def _throttle_pct(pla_list):
        if not pla_list:
            return 0.0
        return round(max(0.0, min(100.0, sum(pla_list) / len(pla_list) / 85.0 * 100.0)), 1)

    def should_log(self) -> bool:
        if self.phase in (Phase.CRUISE,):
            if self.time - self._last_log_t < self._log_interval:
                return False
        self._last_log_t = self.time
        return True

    @property
    def finished(self) -> bool:
        return self.time >= self.flight.duration_s


def load_flight(path: str, perf, airports=None, emergency_fn=None) -> DashlinkReplay:
    return DashlinkReplay(DashlinkFlight(path), perf,
                          airports=airports, emergency_fn=emergency_fn)
