"""What each injector is called, what it takes, and what the sliders should say.

The control panel builds itself from this list, so a new injector shows up in
the UI as soon as it is described here - there is no second place to edit.
Ranges are the ones that produce a plausible fault: the low end should be
survivable and the high end should be clearly an emergency.
"""

# The sensor fault works on whatever the running feed emits - 105 channels on
# a DASHlink replay, 128 on the synthetic generator - so the channel list is
# discovered at runtime rather than written here. See
# noise_injector.describe_channels; the panel fetches it with {"cmd":
# "channels"}.
NOISE_MODES = [
    ("bias",   "Bias drift",  "Reading walks away from truth, up to the magnitude."),
    ("offset", "Fixed offset", "Reading is wrong by a constant amount."),
    ("noise",  "Noise burst", "Channel gets noisy without changing its mean."),
    ("freeze", "Frozen",      "Channel sticks at the value it had when the fault began."),
    ("drop",   "Dropout",     "Channel reads zero, as a dead sensor would."),
    ("spike",  "Spikes",      "Occasional single-sample jumps."),
    ("invert", "Inverted",    "Flips a discrete between 0 and 1."),
]

NOISE_SCALES = [
    ("relative", "% of reading", "Natural for airspeed, N1, fuel flow."),
    ("absolute", "channel units",
     "The only thing that works on a channel sitting at zero - pitch, V/S, "
     "any discrete."),
]


def _n(name, label, lo, hi, step, default, unit, dynamic=None):
    spec = {"name": name, "label": label, "type": "range",
            "min": lo, "max": hi, "step": step, "default": default, "unit": unit}
    if dynamic:
        spec["dynamic"] = dynamic
    return spec


def _choice(name, label, choices, default, dynamic=None):
    """`dynamic` names a list the engine fills in at runtime, for parameters
    whose valid values depend on the feed rather than on this file."""
    spec = {"name": name, "label": label, "type": "choice",
            "choices": choices, "default": default}
    if dynamic:
        spec["dynamic"] = dynamic
    return spec


INJECTORS = [
    {
        "key": "fuel_leak",
        "label": "Fuel Leak",
        "group": "Fuel",
        "blurb": "Drains one tank faster than the engines are burning it.",
        "needs_gen": False,
        "params": [
            _n("rate_lbs_hr", "Leak rate", 50, 3000, 25, 400, "lb/hr"),
            _choice("tank", "Tank", ["left", "right", "center", "all"], "left"),
        ],
    },
    {
        "key": "holding",
        "label": "Unplanned Holding",
        "group": "Fuel",
        "blurb": "ATC delay burning reserve fuel at holding flow.",
        "needs_gen": True,
        "params": [
            _n("duration_s", "Hold duration", 300, 5400, 60, 1800, "s"),
            _n("rate_lbs_hr", "Holding burn", 200, 6000, 50, 1600, "lb/hr"),
        ],
    },
    {
        "key": "ias",
        "label": "Unreliable Airspeed",
        "group": "Air data",
        "blurb": "Pitot blockage - one or both airspeed readings drift off truth.",
        "needs_gen": False,
        "params": [
            _choice("side", "Affected side", ["captain", "fo", "both"], "captain"),
            _n("rate", "Drift rate", -15, 15, 0.5, -3.0, "%/s"),
        ],
    },
    {
        "key": "alt_disagree",
        "label": "Altimeter Disagree",
        "group": "Air data",
        "blurb": "Static port fault - one altimeter walks away from the other.",
        "needs_gen": False,
        "params": [
            _choice("side", "Affected side", ["captain", "fo", "both"], "captain"),
            _n("rate", "Drift rate", -300, 300, 10, 60.0, "ft/s"),
        ],
    },
    {
        "key": "descent",
        "label": "Uncommanded Descent",
        "group": "Flight path",
        "blurb": "Aircraft sinks without a commanded descent.",
        "needs_gen": False,
        "params": [
            _n("rate_fpm", "Sink rate", 100, 4000, 50, 600.0, "fpm"),
        ],
    },
    {
        "key": "windshear",
        "label": "Windshear / Rapid Altitude Loss",
        "group": "Flight path",
        "blurb": "Short violent altitude loss, then recovery. Stops on its own.",
        "needs_gen": False,
        "params": [
            _n("rate_fpm", "Loss rate", 1000, 8000, 250, 4000.0, "fpm"),
            _n("duration_s", "Duration", 5, 60, 1, 15.0, "s"),
        ],
    },
    {
        "key": "energy",
        "label": "Energy Bleed",
        "group": "Flight path",
        "blurb": "Losing height and speed together - the low-energy trap.",
        "needs_gen": False,
        "params": [
            _n("alt_rate_fpm", "Altitude loss", 100, 2000, 50, 400.0, "fpm"),
            _n("spd_rate_kts_s", "Speed loss", 0.1, 3.0, 0.1, 0.5, "kt/s"),
        ],
    },
    {
        "key": "turbulence",
        "label": "Turbulence / Structural G",
        "group": "Flight path",
        "blurb": "Oscillating vertical acceleration - severe turbulence encounter.",
        "needs_gen": False,
        "params": [
            _n("amplitude_fpm", "Amplitude", 100, 2000, 50, 600.0, "fpm"),
            _n("freq_hz", "Frequency", 0.05, 1.0, 0.05, 0.25, "Hz"),
        ],
    },
    {
        "key": "noise",
        "label": "Sensor Fault",
        "group": "Sensors",
        "blurb": "Makes any channel lie - drift, offset, noise, freeze, dropout, "
                 "spikes. Several channels can be faulted at once.",
        "needs_gen": False,
        "multi": "channel",     # keyed by channel, so it holds many at once
        "params": [
            _choice("channel", "Channel", [], "ias_captain", dynamic="channels"),
            _choice("mode", "Fault mode", [m[0] for m in NOISE_MODES], "bias"),
            _choice("scale", "Magnitude is", [s[0] for s in NOISE_SCALES], "relative"),
            # Range depends on the channel picked - Mach needs 0.01 steps where
            # altitude needs 50 - so the engine supplies it with the channel
            # list and the panel resizes the slider. See noise_injector._span_for.
            _n("magnitude", "Magnitude", -200.0, 200.0, 1.0, 20.0, "",
               dynamic="span"),
            _n("rate_per_s", "Rate", -200.0, 200.0, 0.5, 10.0, "/s",
               dynamic="span"),
            _n("element", "Which unit (0 = all)", 0, 8, 1, 0, ""),
        ],
    },
    {
        "key": "notam",
        "label": "NOTAM Closures",
        "group": "Airports",
        "blurb": "Closes airports along the route so GRACE has to work around them.",
        "needs_gen": True,
        "params": [
            _n("count", "How many", 1, 30, 1, 10, "airports"),
            _n("corridor_nm", "Corridor width", 20, 200, 10, 60.0, "nm"),
        ],
    },
]

BY_KEY = {spec["key"]: spec for spec in INJECTORS}


def defaults(key: str) -> dict:
    return {p["name"]: p["default"] for p in BY_KEY[key]["params"]}


def coerce(key: str, params: dict) -> dict:
    """Turn whatever the UI sent into arguments start() will accept.

    Unknown keys are dropped rather than passed through - a stray field from a
    stale browser tab shouldn't raise a TypeError deep inside an injector.
    """
    spec = BY_KEY.get(key)
    if spec is None:
        raise ValueError(f"unknown injector '{key}'")

    out = {}
    for p in spec["params"]:
        if p["name"] not in params:
            continue
        value = params[p["name"]]
        if p["type"] == "range":
            value = float(value)
            if p.get("dynamic"):
                # The real bound belongs to the channel, not to this spec;
                # rounding to the catalog's step here would turn a Mach fault
                # of 0.09 into 0. Only reject what isn't a number at all.
                if value != value or value in (float("inf"), float("-inf")):
                    raise ValueError(f"{key}.{p['name']}: not a finite number")
            else:
                value = max(p["min"], min(p["max"], value))
                if p["step"] >= 1 and float(p["step"]).is_integer():
                    value = int(round(value))
        elif p["type"] == "choice":
            # A dynamic choice is validated by whoever fills it in - the
            # channel list depends on the running feed, not on this file.
            if not p.get("dynamic") and value not in p["choices"]:
                raise ValueError(
                    f"{key}.{p['name']}: '{value}' not one of {p['choices']}")
        out[p["name"]] = value
    return out


def ui_manifest() -> dict:
    """Everything the control panel needs to draw itself."""
    return {
        "injectors": INJECTORS,
        "noise_modes": [
            {"key": k, "label": lab, "blurb": b} for k, lab, b in NOISE_MODES
        ],
        "noise_scales": [
            {"key": k, "label": lab, "blurb": b} for k, lab, b in NOISE_SCALES
        ],
    }
