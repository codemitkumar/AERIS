"""NTSB docket FDR replay.

Replays the tabular FDR attachment from an NTSB public docket
(data.ntsb.gov/Docket, "FLIGHT DATA RECORDER - ATTACHMENT 1 (CSV DATA ...)")
through the same DataBus the synthetic generator feeds.

The CSV layout is the NTSB Vehicle Recorder Division's standard export:

    NTSB DCA22LA178, Attachment 1 to ...     metadata rows ("Key: value")
    ...
    DATA
    Time, Accel Lat, Airspeed Comp, ...      parameter labels
    (EDT), (g), (kts), ...                   units
    , NUMBER, %N(0:0="Air",1:1="Grnd"), ...  numeric / discrete encoding
    19:25:00.015625, , 1.0043, ...           one row per recorder word slot

Rows sit on a fine time grid (64 Hz for a 64 wps recorder) and each carries
only the parameters sampled in that slot, so every channel is sparse. Replay
ticks at a fixed rate and holds the last real sample of each channel, the
same way dashlink_loader does — nothing is interpolated into existence.

Parameter labels differ between recorders and specialists, so they are
matched against _ALIASES; a case's docket.json can add or override labels
under "channel_map" without touching this file.

What an FDR attachment does not carry is left out of the state rather than
invented: there is no fuel quantity, no N1/EGT on many older frames, no
captain/FO split. Modules that need those simply see nothing to evaluate.
"""

import csv
import json
import math
import os
import re
from bisect import bisect_right
from pathlib import Path

from data.ingestion.flight_common import Phase, haversine_nm

_ENGINE_DIR = Path(__file__).resolve().parent.parent.parent


def ntsb_dir() -> Path:
    """Where docket downloads live — AERIS_ENGINE/data/ntsb unless
    AERIS_NTSB_DIR points elsewhere."""
    return Path(os.environ.get("AERIS_NTSB_DIR", _ENGINE_DIR / "data" / "ntsb"))


# canonical channel -> labels seen in NTSB exports (case-insensitive).
_ALIASES = {
    "pressure_alt": ("Altitude Press", "Pressure Altitude", "Altitude Pressure", "Alt Press"),
    "radio_alt":    ("Altitude Radio", "Radio Altitude", "Radio Alt", "Altitude Radio-1"),
    "cas":          ("Airspeed Comp", "Computed Airspeed", "Airspeed Computed", "CAS", "Airspeed"),
    "gs":           ("Ground Spd", "Groundspeed", "Ground Speed"),
    "pitch":        ("Pitch", "Pitch Angle", "Pitch Attitude"),
    "roll":         ("Roll", "Roll Angle", "Bank Angle"),
    "heading":      ("Heading", "Magnetic Heading", "Heading Mag"),
    "heading_true": ("Heading True", "True Heading"),
    "track":        ("Track Angle", "Track Angle True", "Track"),
    "lat":          ("Latitude", "Latitude Pos", "Lat"),
    "lon":          ("Longitude", "Longitude Pos", "Long"),
    "vert_g":       ("Accel Vert", "Vertical Acceleration", "Normal Acceleration", "Accel Normal"),
    "lat_g":        ("Accel Lat", "Lateral Acceleration"),
    "long_g":       ("Accel Long", "Longitudinal Acceleration"),
    "air_ground":   ("Air Grnd", "Air/Ground", "Air-Ground", "Air Ground"),
    "wow_nose":     ("Gear WOW-N", "WOW Nose", "Nose Gear WOW"),
    "wow_main":     ("Gear WOW-M", "WOW Main", "Main Gear WOW", "Gear WOW-L", "Gear WOW-R"),
    "gear_down":    ("Gear Down", "Landing Gear Down", "Gear Dn Lock", "Gear Down & Locked"),
    "flap":         ("Flap Handle Pos", "Flap Position", "Flap Pos", "Flaps", "Flap Handle"),
    "speedbrake":   ("Spd Brk Hdl-Digital", "Speed Brake Handle", "Speedbrake Handle"),
    "wind_spd":     ("Wind Spd", "Wind Speed"),
    "wind_dir":     ("Wind Direction", "Wind Dir"),
    "sat":          ("SAT", "Static Air Temp", "Static Air Temperature"),
    "tat":          ("TAT", "Total Air Temp", "Total Air Temperature"),
    "mach":         ("Mach", "Mach Number"),
    "vs":           ("Vertical Speed", "Altitude Rate", "Inertial Vertical Speed"),
    "aoa":          ("AOA", "Angle of Attack", "AOA-L", "AOA Left"),
    "column":       ("Ctrl Col Pos-Capt", "Control Column Position", "Ctrl Col Pos"),
    "wheel":        ("Ctrl Whl Pos-Capt", "Control Wheel Position", "Ctrl Whl Pos"),
    "rudder":       ("Rudder", "Rudder Position"),
    "rudder_pedal": ("Rudder Ped Pos", "Rudder Pedal Position"),
    "elevator_l":   ("Elevator-L", "Elevator Position-L", "Elevator L"),
    "elevator_r":   ("Elevator-R", "Elevator Position-R", "Elevator R"),
    # Any one of the autopilot channels in command counts as engaged.
    "ap_cmd_l":     ("AP Cmd-L", "Autopilot Engaged", "AP Engaged", "AP Engaged-L"),
    "ap_cmd_c":     ("AP Cmd-C", "AP Engaged-C"),
    "ap_cmd_r":     ("AP Cmd-R", "AP Engaged-R"),
    "at_engaged":   ("A/T_ENGAGE", "Autothrottle Engaged", "A/T Engaged"),
    "toga":         ("A/T_G/A MODE OPER", "TOGA", "Go Around Mode"),
    # Autopilot pitch modes — translated to FlightGenerator's ap_mode words.
    "ap_alt_hold":  ("AP_ALT Hold Mode Oper", "ALT Hold Mode", "AP ALT HOLD"),
    "ap_appr":      ("AP_APPR Mode Oper", "Approach Mode", "AP APPR", "G/S Mode"),
    "ap_to":        ("AP_TO Mode Oper-P", "AP_TO Mode Oper-R", "TO/GA Mode"),
}

# Per-engine channels: canonical -> label patterns with {n} for the engine.
_ENGINE_ALIASES = {
    "n1":  ("Eng{n} N1", "Eng{n} N1 Actual", "N1-{n}", "Engine {n} N1"),
    "n2":  ("Eng{n} N2", "N2-{n}", "Engine {n} N2"),
    "epr": ("Eng{n} EPR Actual", "Eng{n} EPR", "EPR-{n}", "Engine {n} EPR"),
    "egt": ("Eng{n} EGT", "EGT-{n}", "Engine {n} EGT"),
    "ff":  ("Eng{n} Fuel Flow", "Fuel Flow-{n}", "Eng{n} FF"),
    "tra": ("Eng{n} TRA", "TRA-{n}", "Eng{n} Throttle Pos", "Throttle-{n}"),
}

# Discrete state text -> 1. NTSB encodes discretes as %N(0:0="X",1:1="Y");
# the 1-state text is read from that row, these are the fallbacks.
_TRUE_WORDS = {"1", "on", "air", "grnd", "gnd", "engage", "eng", "oper", "dplyd",
               "down", "dn", "ap in ctl", "disc", "hld", "true", "yes"}

# Readings a recorder produces when the source is not computing (NCD) rather
# than a real value. The 757's ADC parks CAS at 384 kt below ~30 kt on the
# ground; read as real that is a 384-kt taxi and a guaranteed overspeed.
_GROUND_CAS_SENTINEL_KTS = 250.0

_FT_PER_INHG = 1000.0
_TRANSITION_ALT_FT = 18000.0   # US transition altitude — STD above, QNH below
_VS_WINDOW_S = 6.0             # vertical speed fitted over this span of real samples
_BOUNCE_MAX_S = 60.0           # ground contact shorter than this is a touch-and-go
# Cruise is everything within this fraction of the climb from the field to
# the peak: an ATC step from FL380 to FL340 is still cruise, the hold at
# FL270 in this arrival is not.
_CRUISE_BAND_FRAC = 0.20

_A0_KTS = 661.4786             # ISA sea-level speed of sound


def _pressure_ratio(h_ft: float) -> float:
    """ISA static pressure ratio P/P0 at pressure altitude h."""
    if h_ft < 36089.0:
        return (1.0 - 6.8755856e-6 * h_ft) ** 5.2558797
    return 0.2233609 * math.exp(-(h_ft - 36089.0) / 20805.8)


def mach_from_cas(cas_kts: float, pressure_alt_ft: float) -> float:
    """Exact subsonic Mach from calibrated airspeed and pressure altitude —
    both are pressure measurements, so no temperature is needed."""
    if cas_kts <= 0:
        return 0.0
    qc_p0 = (1.0 + 0.2 * (cas_kts / _A0_KTS) ** 2) ** 3.5 - 1.0
    qc_p = qc_p0 / _pressure_ratio(pressure_alt_ft)
    return math.sqrt(max(0.0, 5.0 * ((qc_p + 1.0) ** (2.0 / 7.0) - 1.0)))


def tas_from_mach(mach: float, pressure_alt_ft: float, sat_c=None) -> float:
    """TAS from Mach and static air temperature — ISA when SAT isn't recorded."""
    t_k = (sat_c + 273.15) if sat_c is not None else \
        max(216.65, 288.15 - 0.0019812 * pressure_alt_ft)
    return mach * 38.967854 * math.sqrt(t_k)


def _parse_time(text: str):
    """'19:25:00.015625' -> seconds of day."""
    m = re.match(r"\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if not m:
        return None
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def _true_state(encoding: str):
    """Text of the 1-state from '%N(0:0="Air",1:1="Grnd")' -> 'grnd'."""
    m = re.search(r'1:1="([^"]*)"', encoding or "")
    return m.group(1).strip().lower() if m else None


class _Channel:
    __slots__ = ("label", "units", "discrete", "true_text", "t", "v", "_i")

    def __init__(self, label, units, encoding):
        self.label = label
        self.units = units.strip("() ")
        self.discrete = encoding.strip().startswith("%")
        self.true_text = _true_state(encoding) if self.discrete else None
        self.t: list[float] = []
        self.v: list[float] = []
        self._i = -1

    def add(self, t: float, raw: str) -> None:
        raw = raw.strip()
        if not raw:
            return
        if self.discrete:
            low = raw.lower()
            if self.true_text is not None:
                val = 1.0 if low == self.true_text else 0.0
            else:
                val = 1.0 if low in _TRUE_WORDS else 0.0
        else:
            try:
                val = float(raw)
            except ValueError:
                return
            if math.isnan(val):
                return
        self.t.append(t)
        self.v.append(val)

    @property
    def rate_hz(self) -> float:
        if len(self.t) < 2:
            return 0.0
        return (len(self.t) - 1) / max(self.t[-1] - self.t[0], 1e-6)

    def at(self, t: float, default=None):
        """Most recent sample at or before t."""
        i = bisect_right(self.t, t) - 1
        return self.v[i] if i >= 0 else default

    def seek(self, t: float, default=None):
        """at() for monotonically increasing t — O(1) amortised in replay."""
        i, t_arr = self._i, self.t
        n = len(t_arr)
        if i >= 0 and t < t_arr[i]:
            i = -1
        while i + 1 < n and t_arr[i + 1] <= t:
            i += 1
        self._i = i
        return self.v[i] if i >= 0 else default


def load_docket_meta(case_dir: Path) -> dict:
    """docket.json in the case folder, else the "docket" block of the
    committed test fixture tests/ntsb_cases/<ID>.json."""
    case_dir = Path(case_dir)
    for p, key in ((case_dir / "docket.json", None),
                   (_ENGINE_DIR / "tests" / "ntsb_cases" / f"{case_dir.name}.json", "docket")):
        if p.is_file():
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            return data.get(key, {}) if key else data
    return {}


def list_cases() -> list[str]:
    """Docket folders under ntsb_dir() that hold an FDR CSV."""
    root = ntsb_dir()
    if not root.is_dir():
        return []
    return sorted(d.name for d in root.iterdir() if d.is_dir() and find_csv(d))


def find_csv(case_dir: Path) -> Path | None:
    """The FDR attachment in a case folder — the largest CSV there."""
    csvs = sorted(Path(case_dir).glob("*.csv"), key=lambda p: p.stat().st_size, reverse=True)
    return csvs[0] if csvs else None


class NtsbFlight:
    """Parsed NTSB FDR attachment: sparse channels, metadata, phase marks."""

    def __init__(self, path: str, channel_map: dict | None = None):
        self.path = str(path)
        self.name = os.path.basename(self.path)
        self.meta: dict[str, str] = {}
        self.raw: dict[str, _Channel] = {}
        self._read()

        self.channels: dict[str, _Channel] = {}
        self.engines: dict[str, list[_Channel]] = {}
        self._map_channels(channel_map or {})

        if "pressure_alt" not in self.channels:
            raise ValueError(f"{self.name}: no pressure altitude column — not a usable FDR export")

        starts = [c.t[0] for c in self.raw.values() if c.t]
        ends = [c.t[-1] for c in self.raw.values() if c.t]
        self.t0 = min(starts)
        self.t_end = max(ends)
        self.duration_s = self.t_end - self.t0
        self.fastest_rate_hz = max((c.rate_hz for c in self.raw.values()), default=1.0)

        self.air_intervals = self._air_intervals()
        self.phases = self._segment_phases()

    # ── parsing ─────────────────────────────────────────────────────────

    def _read(self) -> None:
        with open(self.path, newline="", encoding="latin-1") as f:
            reader = csv.reader(f)
            header = None
            for row in reader:
                first = row[0].strip() if row else ""
                if first.lower() == "time" and len(row) > 2:
                    header = [h.strip() for h in row]
                    break
                if ":" in first and len(row) <= 3:
                    key, _, val = ",".join(row).partition(":")
                    self.meta[key.strip()] = val.strip()
            if header is None:
                raise ValueError(f"{self.name}: no 'Time' header row")

            units = next(reader, [""] * len(header))
            encodings = next(reader, [""] * len(header))
            # Older exports omit the encoding row — then that row is data.
            pending = None
            if encodings and _parse_time(encodings[0]) is not None:
                pending, encodings = encodings, [""] * len(header)

            cols = [
                (i, _Channel(h, units[i] if i < len(units) else "",
                             encodings[i] if i < len(encodings) else ""))
                for i, h in enumerate(header) if i > 0 and h
            ]
            self.raw = {ch.label: ch for _, ch in cols}

            day_offset, last_t = 0.0, None
            rows = reader if pending is None else _chain([pending], reader)
            for row in rows:
                if not row:
                    continue
                t = _parse_time(row[0])
                if t is None:
                    continue
                if last_t is not None and t + day_offset < last_t - 43200:
                    day_offset += 86400.0      # recording crossed midnight
                t += day_offset
                last_t = t
                for i, ch in cols:
                    if i < len(row) and row[i]:
                        ch.add(t, row[i])

    def _map_channels(self, override: dict) -> None:
        by_lower = {k.lower(): ch for k, ch in self.raw.items() if ch.t}

        def pick(labels):
            for lab in labels:
                ch = by_lower.get(lab.lower())
                if ch is not None:
                    return ch
            return None

        for canon, labels in _ALIASES.items():
            extra = override.get(canon)
            ch = pick(([extra] if isinstance(extra, str) else list(extra or [])) + list(labels))
            if ch is not None:
                self.channels[canon] = ch

        for canon, patterns in _ENGINE_ALIASES.items():
            found = []
            for n in range(1, 5):
                ch = pick([p.format(n=n) for p in patterns])
                if ch is None:
                    break
                found.append(ch)
            if found:
                self.engines[canon] = found

    def has(self, canon: str) -> bool:
        return canon in self.channels

    def value(self, canon: str, t: float, default=None):
        ch = self.channels.get(canon)
        return ch.at(t, default) if ch else default

    # ── flight structure ────────────────────────────────────────────────

    def on_ground_at(self, t: float) -> bool:
        for canon in ("air_ground", "wow_main"):
            ch = self.channels.get(canon)
            if ch is not None:
                v = ch.at(t)
                if v is not None:
                    # "air_ground" is 1 = Grnd; "wow_main" is 1 = on ground.
                    return bool(v) if ch.true_text in (None, "grnd", "gnd", "ground", "on") \
                        else not bool(v)
        ra = self.value("radio_alt", t)
        return ra is not None and ra < 5.0

    def _air_intervals(self) -> list[tuple[float, float]]:
        """[(liftoff_t, touchdown_t), ...] from the air/ground discrete."""
        src = self.channels.get("air_ground") or self.channels.get("wow_main")
        out = []
        if src is None:
            return out
        airborne_at = None
        for t in src.t:
            air = not self.on_ground_at(t)
            if air and airborne_at is None:
                airborne_at = t
            elif not air and airborne_at is not None:
                out.append((airborne_at, t))
                airborne_at = None
        if airborne_at is not None:
            out.append((airborne_at, self.t_end))
        return out

    @property
    def touchdowns(self) -> list[float]:
        return [td for _, td in self.air_intervals if td < self.t_end]

    @property
    def bounces(self) -> list[tuple[float, float]]:
        """Ground contacts followed by liftoff within _BOUNCE_MAX_S —
        touch-and-goes, bounced landings, rejected landings."""
        iv = self.air_intervals
        return [(iv[i][1], iv[i + 1][0]) for i in range(len(iv) - 1)
                if iv[i + 1][0] - iv[i][1] <= _BOUNCE_MAX_S]

    def _segment_phases(self) -> list[tuple[float, Phase]]:
        """Phase marks from the recorded air/ground state and altitude profile.

        Like the DASHlink replay, the whole flight is known up front, so phases
        come from where the peak altitude and the touchdowns actually are.
        A bounce or rejected landing is marked LANDING while on the runway
        and CLIMB for the go-around that follows, back to DESCENT once the
        go-around levels off.
        """
        alt = self.channels["pressure_alt"]
        iv = self.air_intervals
        if not iv:
            return [(self.t0, Phase.CRUISE)]

        first_air, last_td = iv[0][0], iv[-1][1]
        in_air = [(t, a) for t, a in zip(alt.t, alt.v) if first_air <= t <= last_td]
        if not in_air:
            return [(self.t0, Phase.GROUND_ROLL)]
        peak = max(a for _, a in in_air)
        ground = min(a for _, a in in_air)
        band = max(200.0, (peak - ground) * _CRUISE_BAND_FRAC)
        near_peak = [t for t, a in in_air if a > peak - band]
        # The band edges are where the climb enters and the descent leaves
        # it; walk out to where the aircraft actually levelled off and
        # actually started down.
        toc = self._level_edge(near_peak[0], step=+10.0)
        tod = self._level_edge(near_peak[-1], step=-10.0)

        marks = [(self.t0, Phase.GROUND_ROLL), (first_air, Phase.CLIMB),
                 (toc, Phase.CRUISE), (tod, Phase.DESCENT)]
        for td, lo in self.bounces:
            marks.append((td, Phase.LANDING))
            marks.append((lo, Phase.CLIMB))
            marks.append((self._go_around_top(lo), Phase.DESCENT))
        if last_td < self.t_end:
            marks.append((last_td, Phase.LANDING))
        marks.sort(key=lambda m: m[0])

        out = []
        for t, p in marks:
            if out and t <= out[-1][0]:
                out[-1] = (out[-1][0], p)
            else:
                out.append((t, p))
        return out

    def _level_edge(self, t: float, step: float) -> float:
        """Walk from t (forward for top of climb, back for top of descent)
        while the aircraft is still climbing toward / descending from the
        level — more than 150 ft of change over the next 30 s."""
        alt = self.channels["pressure_alt"]
        look = 30.0 if step > 0 else -30.0
        while self.t0 < t + look < self.t_end:
            a0, a1 = alt.at(t), alt.at(t + look)
            if a0 is None or a1 is None:
                break
            climbing_on = step > 0 and a1 - a0 > 150.0
            descending_back = step < 0 and a1 - a0 > 150.0   # was higher 30 s earlier
            if not (climbing_on or descending_back):
                break
            t += step
        return t

    def _go_around_top(self, liftoff_t: float) -> float:
        """First level-off after a go-around: highest altitude before the
        aircraft starts down again, searched over the next 10 minutes."""
        alt = self.channels["pressure_alt"]
        best_t, best_a = liftoff_t, -1e9
        for t, a in zip(alt.t, alt.v):
            if t < liftoff_t:
                continue
            if t > liftoff_t + 600:
                break
            if a > best_a + 20:
                best_t, best_a = t, a
            elif a < best_a - 300:
                break
        return best_t

    def phase_at(self, t: float) -> Phase:
        current = self.phases[0][1]
        for start, phase in self.phases:
            if t >= start:
                current = phase
            else:
                break
        return current

    def clock(self, t: float) -> str:
        """Seconds-of-day -> 'HH:MM:SS' in the recording's own time base."""
        t = t % 86400
        return f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{int(t % 60):02d}"


def _chain(*its):
    for it in its:
        yield from it


class NtsbReplay:
    """Replays an NtsbFlight with the surface main.py and the emergency
    injectors expect — the same one DashlinkReplay provides.

    `time` in the published state is seconds since the start of the
    recording; `fdr_time_s` / `fdr_clock` carry the docket's own time base
    (local time for NTSB exports) so alerts can be lined up with the report.
    """

    def __init__(self, flight: NtsbFlight, perf, airports=None, meta=None,
                 emergency_fn=None, rate_hz: float = 4.0):
        self.flight = flight
        self.perf = perf
        self.meta = meta or {}
        self.emergency_fn = emergency_fn
        self.region = "US"
        self.airports = airports or []

        self.dt = 1.0 / rate_hz
        self.time = 0.0
        self.phase = Phase.GROUND_ROLL
        self._last_log_t = -1e9
        self._log_interval = 60.0

        self.qnh_inhg = float(self.meta.get("qnh_inhg", 29.92))
        self.field_elev_ft = self.meta.get("destination_elev_ft")

        # No fuel on an FDR attachment; injectors still expect the attributes.
        self.fuel_total_lbs = float(self.meta.get("fuel_on_board_lbs", 0.0))
        self._initial_fuel_lbs = max(1.0, self.fuel_total_lbs)
        self.fuel_used_lbs = 0.0
        self.fuel = 100.0
        self.fuel_left_lbs = self.fuel_total_lbs / 2.0
        self.fuel_right_lbs = self.fuel_total_lbs / 2.0
        self.fuel_center_lbs = 0.0
        self.fuel_flow_total_lbs_hr = 0.0

        f, t0 = flight, flight.t0
        self.alt = f.value("pressure_alt", t0, 0.0)
        self.speed = f.value("cas", t0, 0.0)
        self.vs = 0.0
        self.lat = f.value("lat", t0 + 30, 0.0)
        self.lon = f.value("lon", t0 + 30, 0.0)
        self.heading = f.value("heading", t0, 0.0)

        self.origin, self.dest = self._endpoints()
        if self.field_elev_ft is None:
            self.field_elev_ft = self.dest.get("elev_ft", 0.0)
        self.route_distance_nm = (
            haversine_nm(self.origin["lat"], self.origin["lon"],
                         self.dest["lat"], self.dest["lon"])
            if self.origin.get("lat") and self.dest.get("lat") else 0.0
        )
        self.departure_heading = f.value("heading", f.air_intervals[0][0], 0.0) \
            if f.air_intervals else self.heading
        self.landing_heading = f.value("heading", f.touchdowns[-1], 0.0) \
            if f.touchdowns else self.heading
        self._cruise_speed = max(1.0, self._median_cruise_gs())
        self.alternates = []

    # ── metadata ────────────────────────────────────────────────────────

    def _airport(self, icao):
        for a in self.airports:
            if a.get("icao") == icao:
                return a
        return None

    def _nearest_airport(self, lat, lon):
        if not self.airports or not lat:
            return None
        best, best_d = None, 1e9
        for a in self.airports:
            if a.get("lat") is None:
                continue
            d = haversine_nm(lat, lon, a["lat"], a["lon"])
            if d < best_d:
                best, best_d = a, d
        return best if best_d < 10.0 else None

    def _endpoints(self):
        """Origin/destination from docket.json if given, else from where the
        recorded track starts and ends."""
        f = self.flight
        fallback = {"icao": "", "iata": "", "lat": 0.0, "lon": 0.0, "name": ""}
        o = self._airport(self.meta.get("origin_icao")) or \
            self._nearest_airport(f.value("lat", f.t0 + 30), f.value("lon", f.t0 + 30))
        d = self._airport(self.meta.get("destination_icao")) or \
            self._nearest_airport(f.value("lat", f.t_end), f.value("lon", f.t_end))
        return o or fallback, d or fallback

    def _median_cruise_gs(self) -> float:
        ch = self.flight.channels.get("gs")
        if not ch:
            return 1.0
        d = sorted(v for v in ch.v if v > 100)
        return d[len(d) // 2] if d else 1.0

    # ── replay ──────────────────────────────────────────────────────────

    def _baro_alt(self, pressure_alt: float) -> float:
        """What the altimeter shows: pressure altitude corrected to QNH below
        the transition altitude (the FDR records it uncorrected, on 29.92)."""
        if pressure_alt < _TRANSITION_ALT_FT:
            return pressure_alt + (self.qnh_inhg - 29.92) * _FT_PER_INHG
        return pressure_alt

    def _vertical_speed(self, t: float) -> float:
        """Least-squares slope of the real pressure-altitude samples in the
        last _VS_WINDOW_S. Differencing held ticks instead turns every 1 Hz
        sample step into a spike that reads as a g-jerk."""
        ch = self.flight.channels["pressure_alt"]
        hi = bisect_right(ch.t, t)
        lo = bisect_right(ch.t, t - _VS_WINDOW_S)
        n = hi - lo
        if n < 3:
            return 0.0
        ts, hs = ch.t[lo:hi], ch.v[lo:hi]
        tm, hm = sum(ts) / n, sum(hs) / n
        den = sum((x - tm) ** 2 for x in ts)
        if den <= 0:
            return 0.0
        return sum((x - tm) * (y - hm) for x, y in zip(ts, hs)) / den * 60.0

    @staticmethod
    def _ap_engaged(v) -> bool:
        return any(v(k, 0.0) for k in ("ap_cmd_l", "ap_cmd_c", "ap_cmd_r"))

    def _ap_mode(self, v, vs: float) -> str:
        """Autopilot pitch-mode discretes -> the ap_mode words the modules
        know (FlightGenerator's CLB / CRZ / DES / APP / TOGA / OFF)."""
        if not self._ap_engaged(v):
            return "TOGA" if v("toga", 0.0) and vs > 300 else "OFF"
        if v("ap_appr", 0.0):
            return "APP"
        if v("ap_to", 0.0):
            return "TOGA"
        if v("ap_alt_hold", 0.0):
            return "CRZ"
        # VNAV / FL CH, altitude capture (still closing on the level), or a
        # mode this frame doesn't record: all of them command the vertical
        # path, so the direction of travel is the mode.
        return "DES" if vs < -300 else "CLB" if vs > 300 else "CRZ"

    def step(self) -> dict:
        f = self.flight
        t = f.t0 + self.time

        def v(canon, default=None):
            ch = f.channels.get(canon)
            return ch.seek(t, default) if ch else default

        on_ground = f.on_ground_at(t)
        p_alt = v("pressure_alt", 0.0)
        alt = self._baro_alt(p_alt)
        ra = v("radio_alt")
        gs = v("gs", 0.0)
        cas = v("cas", 0.0)
        if on_ground and cas > _GROUND_CAS_SENTINEL_KTS and gs < 60:
            cas = 0.0           # ADC not computing — see _GROUND_CAS_SENTINEL_KTS
        rec_vs = v("vs")
        vs = rec_vs if rec_vs is not None else self._vertical_speed(t)
        pitch = v("pitch", 0.0)
        roll = v("roll", 0.0)
        heading = v("heading", self.heading)
        lat = v("lat", self.lat)
        lon = v("lon", self.lon)

        state: dict = {
            "time": round(self.time, 3),
            "dt": self.dt,
            "sample_rate_hz": round(1.0 / self.dt, 4),
            "source": "ntsb",
            "source_file": f.name,
            "ntsb_id": self.meta.get("ntsb_id", ""),
            "fdr_time_s": round(t, 3),
            "fdr_clock": f.clock(t),

            "pressure_alt_ft": p_alt,
            "alt_captain": alt, "alt_fo": alt,
            "cas_kts": cas, "ias_captain": cas, "ias_fo": cas,
            "groundspeed_kts": gs, "gs_kts": gs,
            "vs_captain": vs, "vs_fo": vs, "altitude_rate_fpm": vs,
            "pitch_deg": pitch, "pitch_captain": pitch, "pitch_fo": pitch,
            "roll_deg": roll, "roll_captain": roll, "roll_fo": roll, "bank_deg": roll,
            "heading_deg": heading, "heading_captain": heading, "heading_fo": heading,
            "hdg_deg": heading, "magnetic_heading_deg": heading,
            "lat": lat, "lon": lon,
            "weight_on_wheels": int(on_ground),
        }
        if ra is not None:
            state["radio_alt_ft"] = ra
        if self.field_elev_ft is not None:
            state["field_elev_ft"] = self.field_elev_ft
            state["height_aal_ft"] = round(alt - self.field_elev_ft, 1)

        optional = {
            "track": ("track_deg",), "mach": ("mach",), "sat": ("oat_c",),
            "tat": ("tat_c",), "aoa": ("aoa_deg",), "vert_g": ("load_factor_g", "g_load"),
            "lat_g": ("lateral_g",), "long_g": ("longitudinal_g",),
            "wind_spd": ("wind_spd_kts", "wind_speed_kts"), "wind_dir": ("wind_dir_deg",),
            "column": ("control_column_capt",), "wheel": ("control_wheel_capt",),
            "rudder": ("rudder_deg",), "rudder_pedal": ("rudder_pedal_pos",),
            "speedbrake": ("speedbrake_handle",),
        }
        for canon, keys in optional.items():
            val = v(canon)
            if val is not None:
                for k in keys:
                    state[k] = val

        # Mach and TAS are rarely on the frame but every speed/energy module
        # wants them. Mach is exact from CAS + pressure altitude; TAS needs
        # temperature, ISA when SAT isn't recorded.
        if "mach" not in state:
            state["mach"] = round(mach_from_cas(cas, p_alt), 4)
            state["mach_derived"] = True
        if "tas_kts" not in state:
            state["tas_kts"] = round(tas_from_mach(state["mach"], p_alt, state.get("oat_c")), 1)
            state["tas_derived"] = True

        for canon, key in (("n1", "n1_pct"), ("n2", "n2_pct"), ("epr", "epr"),
                           ("egt", "egt_c"), ("ff", "fuel_flow_pph"),
                           ("tra", "power_lever_angle_deg")):
            chans = f.engines.get(canon)
            if chans:
                vals = [c.seek(t) for c in chans]
                if all(x is not None for x in vals):
                    state[key] = vals

        # ── configuration ───────────────────────────────────────────────
        flap = v("flap", 0.0)
        gear_rec = v("gear_down")
        # Few FDR frames record the gear lever. Without it, landing flap or
        # being on the runway is the best available evidence the gear is down.
        gear_down = bool(gear_rec) if gear_rec is not None else (
            on_ground or flap >= float(self.meta.get("gear_down_flap_deg", 20.0)))
        state.update({
            "flap_deg": flap,
            "gear": "DOWN" if gear_down else "UP",
            "gear_down_locked": int(gear_down),
            "gear_inferred": gear_rec is None,
            "throttle_pct": self._throttle_pct(state.get("power_lever_angle_deg")),
            "ap_engaged": int(self._ap_engaged(v)),
            "at_engaged": int(bool(v("at_engaged", 0.0))),
            "ap_mode": self._ap_mode(v, vs),
        })

        # ── fuel — not on the FDR; only what docket.json states ──────────
        state.update({
            "fuel_available": "fuel_on_board_lbs" in self.meta,
            "fuel_total_lbs": round(self.fuel_total_lbs, 1),
            "fuel_left_lbs": round(self.fuel_left_lbs, 1),
            "fuel_right_lbs": round(self.fuel_right_lbs, 1),
            "fuel_center_lbs": round(self.fuel_center_lbs, 1),
            "fuel_flow_total_lbs_hr": 0.0,
            "fuel_reserve_min_lbs": 0.0,
        })

        # ── phase / route ───────────────────────────────────────────────
        self.phase = f.phase_at(t)
        self.alt, self.speed, self.vs = alt, cas, vs
        self.lat, self.lon, self.heading = lat, lon, heading
        dest = self.dest
        state.update({
            "phase": self.phase.name,
            "origin_icao": self.origin.get("icao", ""),
            "destination_icao": dest.get("icao", ""),
            "dist_to_dest_nm": round(haversine_nm(lat, lon, dest["lat"], dest["lon"]), 2)
            if dest.get("lat") else 0.0,
        })

        p = self.perf
        state.update({
            "v1_kts": p.v1_kts, "vr_kts": p.vr_kts, "v2_kts": p.v2_kts,
            "vref_kts": float(self.meta.get("vref_kts", p.vref_kts)),
            "best_glide_kts": p.best_glide_kts,
            "qnh_hpa": round(self.qnh_inhg * 33.8639, 1),
            "baro_setting_captain_hpa": round(self.qnh_inhg * 33.8639, 1)
            if p_alt < _TRANSITION_ALT_FT else 1013.25,
            "baro_setting_fo_hpa": round(self.qnh_inhg * 33.8639, 1)
            if p_alt < _TRANSITION_ALT_FT else 1013.25,
        })

        if self.emergency_fn:
            self.emergency_fn(state, self)

        self.time += self.dt
        return state

    @staticmethod
    def _throttle_pct(tra_list):
        """TRA in degrees -> 0-100 %, against a typical 0-80 deg idle-to-max
        resolver travel."""
        if not tra_list:
            return 0.0
        return round(max(0.0, min(100.0, sum(tra_list) / len(tra_list) / 80.0 * 100.0)), 1)

    def should_log(self) -> bool:
        if self.phase == Phase.CRUISE and self.time - self._last_log_t < self._log_interval:
            return False
        self._last_log_t = self.time
        return True

    @property
    def finished(self) -> bool:
        return self.time >= self.flight.duration_s


def load_case(case: str, perf=None, airports=None, emergency_fn=None,
              rate_hz: float = 4.0, meta: dict | None = None) -> NtsbReplay:
    """Replay for a docket folder name ('DCA22LA178') or a path to one, or to
    the CSV itself. Route, QNH and aircraft come from `meta`, else from a
    docket.json next to the CSV."""
    from data.ingestion.aircraft_performance import get_performance

    p = Path(case)
    if not p.exists():
        p = ntsb_dir() / case
    case_dir = p if p.is_dir() else p.parent
    csv_path = find_csv(p) if p.is_dir() else p
    if csv_path is None:
        raise FileNotFoundError(f"No FDR CSV in {case_dir}")

    if meta is None:
        meta = load_docket_meta(case_dir)
    flight = NtsbFlight(csv_path, channel_map=meta.get("channel_map"))
    if perf is None:
        perf = get_performance(meta.get("perf_model", "737"),
                               airport_elev_ft=float(meta.get("origin_elev_ft", 0.0)))
    return NtsbReplay(flight, perf, airports=airports, meta=meta,
                      emergency_fn=emergency_fn, rate_hz=rate_hz)
