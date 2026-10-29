import random


class ChannelFault:
    """One failing channel.

    Modes
    -----
    bias    reading walks away from truth at `rate` per second, capped at
            `magnitude` - a slow calibration drift
    offset  a fixed `magnitude` applied from the moment it starts
    noise   zero-mean noise of size `magnitude`
    freeze  channel sticks at the value it held when the fault began
    drop    channel reads zero, as a dead sensor or lost data frame would
    spike   single-sample jumps of `magnitude`, about `rate` times a second
    invert  flips a discrete between 0 and 1

    `scale` decides what `magnitude` means. "relative" is a percentage of the
    current reading, which is the natural way to describe an airspeed or N1
    fault. "absolute" is in the channel's own units, and is the only thing that
    works on a channel sitting at or near zero - pitch, roll, vertical speed and
    every discrete read 0 for long stretches, and a percentage of zero is zero.
    """

    MODES = ("bias", "offset", "noise", "freeze", "drop", "spike", "invert")

    def __init__(self, channel, mode="bias", magnitude=10.0, rate=5.0,
                 scale="relative", element=0):
        if mode not in self.MODES:
            raise ValueError(f"unknown fault mode '{mode}'")
        if scale not in ("relative", "absolute"):
            raise ValueError(f"scale must be 'relative' or 'absolute', got '{scale}'")

        self.channel = channel
        self.mode = mode
        self.magnitude = float(magnitude)
        self.rate = float(rate)
        self.scale = scale
        self.element = int(element)     # 0 = every element of a vector channel

        self._drift = 0.0
        self._held = None

    def describe(self) -> dict:
        return {
            "channel": self.channel, "mode": self.mode,
            "magnitude": self.magnitude, "rate": self.rate,
            "scale": self.scale, "element": self.element,
            "drift": round(self._drift, 4),
        }

    # ── application ──────────────────────────────────────────────────────────

    def apply(self, state: dict, dt: float) -> None:
        holder, key = _resolve(state, self.channel)
        if holder is None:
            return

        value = holder[key]
        if isinstance(value, list):
            holder[key] = [
                self._one(v, dt, i) if self._hits(i) else v
                for i, v in enumerate(value)
            ]
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            holder[key] = self._one(value, dt, 0)
        elif isinstance(value, bool):
            out = self._one(float(value), dt, 0)
            holder[key] = bool(round(out))

    def _hits(self, index: int) -> bool:
        return self.element == 0 or self.element == index + 1

    def _one(self, value, dt, index):
        # Freeze and drop ignore magnitude entirely.
        if self.mode == "freeze":
            if self._held is None:
                self._held = value
            return self._held
        if self.mode == "drop":
            return 0.0
        if self.mode == "invert":
            return 0.0 if value else 1.0

        step = self._amount(value)

        if self.mode == "offset":
            return value + step

        if self.mode == "bias":
            cap = abs(step)
            growth = self._amount(value, self.rate) * dt
            # Drift accumulates in the channel's own units either way; for a
            # relative fault that means it tracks the reading as it changes.
            self._drift = _toward(self._drift, growth, cap)
            return value + self._drift

        if self.mode == "noise":
            return value + random.gauss(0.0, abs(step))

        if self.mode == "spike":
            if random.random() < self.rate * dt:
                return value + random.choice((-1.0, 1.0)) * abs(step)
            return value

        return value

    def _amount(self, value, size=None):
        """How much to move the reading, in the channel's own units."""
        size = self.magnitude if size is None else size
        if self.scale == "relative":
            return value * size / 100.0
        return size


def _toward(current, growth, cap):
    """Grow `current` by `growth`, never past ±cap."""
    nxt = current + growth
    if nxt > cap:
        return cap
    if nxt < -cap:
        return -cap
    return nxt


def _resolve(state: dict, path: str):
    """Find the dict holding a channel, following one level of dotted path.

    'fdr_discretes.GPWS' reaches into the discretes block, which is how the
    recorder's 33 discrete channels become injectable without flattening them
    into the top-level state.
    """
    if "." in path:
        head, tail = path.split(".", 1)
        holder = state.get(head)
        if isinstance(holder, dict) and tail in holder:
            return holder, tail
        return None, None
    return (state, path) if path in state else (None, None)


class NoiseInjector:
    """Sensor faults, on any channel, several at once.

    The other injectors move the aircraft; this one makes a channel lie about
    it. That distinction is what makes it usable on replayed FDR data, where
    the aircraft already did what it did and cannot be made to do anything
    else - but any of its 105 recorded channels can still misreport.

    Faults are keyed by channel, so corrupting airspeed and freezing N1 at the
    same time is two calls to start(), not a choice between them.
    """

    NAME = "Sensor Fault"

    def __init__(self):
        self.faults: dict[str, ChannelFault] = {}
        # Refreshed from the live feed so the control panel can offer whatever
        # the current source actually emits, rather than a list written by hand
        # that goes stale the moment a channel is added.
        self.available: list[dict] = []
        self._seen_shape = None
        self._since_refresh = 0

    @property
    def active(self) -> bool:
        return bool(self.faults)

    def start(self, channel: str = "ias_captain", mode: str = "bias",
              magnitude: float = 10.0, rate_per_s: float = 5.0,
              scale: str = "relative", element: int = 0) -> None:
        # Only check once the feed has told us what exists. A typo would
        # otherwise sit there as an active fault doing nothing at all, which
        # looks exactly like a detection module failing to notice it.
        known = {c["key"] for c in self.available}
        if known and channel not in known:
            raise ValueError(
                f"'{channel}' is not a channel on this feed "
                f"(it carries {len(known)}); try 'channels' to list them")

        fault = ChannelFault(channel, mode, magnitude, rate_per_s, scale, element)
        self.faults[_fault_key(channel, element)] = fault
        where = f"[{element}]" if element else ""
        unit = "%" if scale == "relative" else "units"
        print(f"[INJECT] sensor_fault  {channel}{where}  mode={mode}  "
              f"magnitude={magnitude:g} {unit}  rate={rate_per_s:g}/s")

    def stop(self, channel: str | None = None, element: int = 0) -> None:
        if channel is None:
            self.faults.clear()
            print("[INJECT] sensor_fault cleared (all channels)")
            return
        key = _fault_key(channel, element)
        if self.faults.pop(key, None) is not None:
            print(f"[INJECT] sensor_fault cleared ({key})")

    def status(self) -> list[dict]:
        return [f.describe() for f in self.faults.values()]

    def __call__(self, state: dict, gen) -> None:
        self._note_channels(state)
        if not self.faults:
            return
        dt = getattr(gen, "dt", 1.0 / 30.0)
        for fault in self.faults.values():
            fault.apply(state, dt)

    # ── channel discovery ────────────────────────────────────────────────────

    _REFRESH_TICKS = 128

    def _note_channels(self, state: dict) -> None:
        """Keep the injectable-channel list current.

        Rebuilt when the feed's shape changes, and otherwise every so often so
        the sample readings the panel displays don't date from the moment the
        engine started. Not every tick: at 16 Hz over a five-hour flight that
        would be a quarter of a million rebuilds to show one number.
        """
        self._since_refresh += 1
        shape = len(state)
        if shape == self._seen_shape and self._since_refresh < self._REFRESH_TICKS:
            return
        self._seen_shape = shape
        self._since_refresh = 0
        self.available = describe_channels(state)


def _fault_key(channel: str, element: int) -> str:
    return f"{channel}[{element}]" if element else channel


# Keys that describe the run rather than the aircraft. Corrupting the clock or
# the sample rate breaks every module's time base at once, which tells you
# nothing about detection.
_NOT_A_SENSOR = {
    "time", "dt", "sample_rate_hz", "source", "source_file",
    "origin_icao", "destination_icao", "phase",
}

_UNITS = [
    ("_lbs_hr", "lb/hr"), ("_pph", "lb/hr"), ("_lbs", "lb"), ("_kts", "kt"), ("_kias", "kt"),
    ("_fpm", "fpm"), ("_ft", "ft"), ("_deg", "deg"), ("_pct", "%"),
    ("_psi", "psi"), ("_hpa", "hPa"), ("_inhg", "inHg"), ("_mb", "mb"),
    ("_nm", "nm"), ("_hz", "Hz"), ("_ips", "ips"), ("_ddm", "ddm"),
    ("_g", "g"), ("_c", "degC"), ("_ratio", ""), ("_code", ""),
]

# Reading these as a percentage is meaningless: they sit at or cross zero for
# most of a flight, so the panel should default them to absolute units.
_ZERO_CENTRED = (
    "pitch", "roll", "vs_", "_vs_", "aileron", "elevator", "rudder", "trim",
    "aoa", "_g", "glideslope", "localizer", "drift_angle", "fpa",
    "spoiler", "speedbrake", "brake", "control_column", "control_wheel",
    "airbrake", "flap", "gear_", "squat", "weight_on_wheels", "marker",
    "master_warning", "tcas", "reverse", "toga", "anti_ice", "autopilot",
    "hyd_press", "lat", "lon",
)


# How big a fault is worth offering, per unit. Driven by the unit rather than
# by the current reading: airspeed reads 0 on the stand, and sizing its slider
# from that would offer a ±2 kt range for the rest of the flight.
_SPAN_BY_UNIT = {
    "ft":    [-5_000.0, 5_000.0, 10.0],
    "kt":    [-200.0,   200.0,   1.0],
    "fpm":   [-5_000.0, 5_000.0, 50.0],
    "deg":   [-45.0,    45.0,    0.5],
    "%":     [-100.0,   100.0,   1.0],
    "lb":    [-5_000.0, 5_000.0, 25.0],
    "lb/hr": [-5_000.0, 5_000.0, 25.0],
    "psi":   [-200.0,   200.0,   1.0],
    "degC":  [-500.0,   500.0,   5.0],
    "g":     [-5.0,     5.0,     0.1],
    "hPa":   [-100.0,   100.0,   1.0],
    "mb":    [-100.0,   100.0,   1.0],
    "inHg":  [-5.0,     5.0,     0.05],
    "nm":    [-500.0,   500.0,   5.0],
    "ips":   [-10.0,    10.0,    0.1],
    "ddm":   [-1.0,     1.0,     0.01],
    "Hz":    [-20.0,    20.0,    0.5],
}


def _span_for(kind: str, unit: str, sample: float) -> list:
    """A magnitude range that suits this channel, as [min, max, step].

    One fixed slider cannot serve Mach (0.01 matters), altitude (5 000 ft) and
    fuel (900 lb) at once - a step of 1 rounds a Mach fault to nothing and a
    ceiling of 500 clips an altitude one. Ranges run negative as well as
    positive: biasing a channel down is as ordinary a failure as biasing it up.
    """
    if kind == "discrete":
        return [0, 1, 1]
    if unit in _SPAN_BY_UNIT:
        return list(_SPAN_BY_UNIT[unit])

    # Unitless (mach, ratios, counts) - fall back to the size of the reading.
    size = abs(sample)
    for ceiling, (limit, step) in (
        (2.0,      (1.0,      0.01)),
        (200.0,    (200.0,    0.5)),
        (5_000.0,  (5_000.0,  10.0)),
    ):
        if size < ceiling:
            return [-limit, limit, step]
    return [-50_000.0, 50_000.0, 100.0]


# The dual-crew channels carry the instrument in the name instead of the unit
# (ias_captain, not ias_captain_kts), so the suffix rule can't reach them.
_UNIT_BY_STEM = {
    "ias": "kt", "alt": "ft", "vs": "fpm",
    "lat": "deg", "lon": "deg",
    "pitch": "deg", "roll": "deg", "heading": "deg",
}

# Anything that is really a 0/1 flag, and the handful of genuinely unitless
# quantities. Listed so the panel offers a sane slider rather than guessing
# from whatever the reading happened to be when it asked.
_FLAG_KEYS = {
    "gear_down_locked", "gear_up_locked", "hyd_press_green_ok",
    "hyd_press_yellow_ok", "marker_beacon", "master_warning",
    "squat_switch_left", "squat_switch_right", "tcas_advisory",
    "weight_on_wheels", "reverse_active", "toga_active", "autopilot",
    "anti_ice", "spoilers", "speedbrake",
}


def _unit_for(key: str) -> str:
    for suffix, unit in _UNITS:
        if key.endswith(suffix):
            return unit
    if key in ("lat", "lon"):
        return "deg"
    stem = key.rsplit("_", 1)[0] if key.endswith(("_captain", "_fo")) else None
    if stem in _UNIT_BY_STEM:
        return _UNIT_BY_STEM[stem]
    return ""


def _group_for(key: str) -> str:
    if key.startswith("fdr_discretes."):
        return "Recorder discretes"
    for stem, group in (
        ("fuel", "Fuel"), ("n1", "Engine"), ("n2", "Engine"), ("egt", "Engine"),
        ("eng_", "Engine"), ("power_lever", "Engine"), ("throttle", "Engine"),
        ("engine_eff", "Engine"),
        ("ias", "Air data"), ("cas", "Air data"), ("tas", "Air data"),
        ("eas", "Air data"), ("mach", "Air data"), ("press", "Air data"),
        ("aoa", "Air data"), ("oat", "Air data"), ("tat", "Air data"),
        ("qnh", "Air data"), ("baro", "Air data"), ("density", "Air data"),
        ("alt", "Altitude"), ("vs_", "Altitude"), ("pressure_alt", "Altitude"),
        ("cabin", "Pressurisation"),
        ("pitch", "Attitude"), ("roll", "Attitude"), ("heading", "Attitude"),
        ("track", "Attitude"), ("load_factor", "Attitude"),
        ("lateral_g", "Attitude"), ("longitudinal_g", "Attitude"),
        ("aileron", "Controls"), ("elevator", "Controls"), ("rudder", "Controls"),
        ("trim", "Controls"), ("flap", "Controls"), ("spoiler", "Controls"),
        ("speedbrake", "Controls"), ("airbrake", "Controls"),
        ("control_", "Controls"), ("brake", "Controls"), ("gear", "Controls"),
        ("selected_", "Guidance"), ("localizer", "Guidance"),
        ("glideslope", "Guidance"), ("v1_", "Guidance"), ("v2_", "Guidance"),
        ("vr_", "Guidance"), ("vref", "Guidance"), ("best_glide", "Guidance"),
        ("wind", "Environment"), ("crosswind", "Environment"),
        ("headwind", "Environment"),
    ):
        if key.startswith(stem):
            return group
    return "Other"


def describe_channels(state: dict) -> list[dict]:
    """Every channel in this state dict that a sensor fault can act on.

    Text fields are skipped - there is no meaningful way to add noise to
    "DESCENT" - and so are the bookkeeping keys in _NOT_A_SENSOR.
    """
    out = []

    def add(key, value, nested=False):
        if key in _NOT_A_SENSOR or (not nested and isinstance(value, str)):
            return
        if isinstance(value, list):
            if not value or not all(isinstance(v, (int, float)) for v in value):
                return
            kind, size = "vector", len(value)
        elif isinstance(value, bool):
            kind, size = "discrete", 1
        elif isinstance(value, (int, float)):
            # The recorder's discretes arrive as floats, not bools, and so do
            # several generator flags, so these need naming rather than
            # type-checking to be recognised.
            kind = ("discrete"
                    if key.startswith("fdr_discretes.") or key in _FLAG_KEYS
                    else "scalar")
            size = 1
        else:
            return

        prefers_absolute = kind == "discrete" or any(
            s in key for s in _ZERO_CENTRED)
        sample = value[0] if isinstance(value, list) else float(value)
        unit = "" if kind == "discrete" else _unit_for(key)
        out.append({
            "key": key,
            "kind": kind,
            "size": size,
            "unit": unit,
            "group": _group_for(key),
            "default_scale": "absolute" if prefers_absolute else "relative",
            "sample": round(sample, 4),
            "span": _span_for(kind, unit, sample),
        })

    for key, value in state.items():
        if isinstance(value, dict):
            # Module output blocks (assessment, diversion_recommendation) are
            # written after the injectors run, so they never appear here; the
            # recorder's discretes do.
            for sub, sub_value in value.items():
                add(f"{key}.{sub}", sub_value, nested=True)
        else:
            add(key, value)

    out.sort(key=lambda c: (c["group"], c["key"]))
    return out
