"""GRACE — a Graceful-Relaxation Algorithm for Aircraft Emergency Navigation.

Given the live flight state, the loaded airport dataset, and an emergency
severity tier (LOW / MODERATE / HIGH), GRACE ranks every reachable airport
and returns the top N diversion candidates: a plug-and-play scoring engine
that finds the best divert options given where the aircraft is *right now*,
where it can still point given its current condition, and how much fuel it
actually has to get there.

The "graceful" half of the name is the load-bearing part: when full-strictness
worst-case assumptions leave nothing reachable, GRACE doesn't return an empty
list — it re-tries at progressively looser fuel/turn safety margins (see
_RELAXATION_LEVELS) until something survives, labeling every degraded result
so a relaxed pick is never mistaken for a fully-compliant one. That grace
has bounds, though: required runway length and NOTAM closures are never
relaxed, at any tier — see the Modeling notes below.

Designed to be extended without touching the core algorithm:

    New scoring factor:       DiversionSelector.register_factor(...)
    New severity weight set:  DiversionSelector.set_severity_weights(...)
    New emergency condition:  DiversionSelector.register_condition_modifier(...)

Severity is expected to come from an upstream classifier — today, the
rule-based AircraftHealthModule's `overallRisk`; tomorrow, an AI module.
GRACE only ever consumes the resulting "LOW"/"MODERATE"/"HIGH" string plus
a list of active condition IDs (alert IDs), so swapping or upgrading that
classifier requires no changes here — see decision/decision_engine.py for
the current wiring.

Modeling notes
---------------
- Reachability is evaluated from the aircraft's *current* position, not the
  origin — this is a live diversion query, not a pre-flight dispatch plan.
- "Direction of flight" is handled with a turn budget: each active
  condition can restrict max_turn_deg (e.g. a directional-control-loss
  condition might cap it at 45 degrees). Airports requiring a bigger turn
  than the budget allows are excluded outright — "if the rudder is gone,
  some airports are simply unreachable" — while within the allowed budget,
  smaller turns still score higher, since turning costs fuel and time
  (modeled as a small inflation of effective diversion distance).
- Fuel reachability is evaluated worst-case: each active condition can
  raise fuel_burn_multiplier (e.g. a suspected fuel leak assumes the leak
  rate could double before touchdown) and/or fuel_margin_buffer (extra
  reserve required on top of the standard ICAO reserve already budgeted in
  fuel_reserve_min_lbs). Airports that can't be reached under that
  worst-case burn rate, with reserve intact, are excluded — not merely
  penalized.
- NOTAM closures (see emergencyInjector.notam / data.ingestion.notam_reader)
  are read straight out of `state`: a fully closed airport is excluded; an
  airport with a closed runway that still has a usable secondary runway is
  kept, but scored on its remaining usable runway length only.
- Required runway length (_MIN_LANDABLE_RATIO) is a hard, non-negotiable
  floor — never excluded, only ever a physical fact. It sits in the exact
  same "never relax this" category as NOTAM closures, not with the fuel/turn
  safety margins below.
- If nothing survives at full strictness, select() retries at progressively
  looser fuel/turn margins (see _RELAXATION_LEVELS) rather than returning
  nothing — but that relaxation only ever touches the *safety padding* on
  top of the standard reserve and the achievable turn, never the runway
  floor and never a NOTAM closure. Every returned candidate says so via its
  `relaxed`/`relaxation_notes` fields, so a degraded pick is never silently
  indistinguishable from a fully-compliant one.
"""

import math
from copy import deepcopy

_EARTH_R_NM = 3440.065


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _haversine_nm(lat1, lon1, lat2, lon2):
    lat1r, lat2r = math.radians(lat1), math.radians(lat2)
    dlat = lat2r - lat1r
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1r) * math.cos(lat2r) * math.sin(dlon / 2) ** 2
    return _EARTH_R_NM * 2 * math.asin(math.sqrt(a))


def _bearing_deg(lat1, lon1, lat2, lon2):
    dlon = math.radians(lon2 - lon1)
    y = math.sin(dlon) * math.cos(math.radians(lat2))
    x = (math.cos(math.radians(lat1)) * math.sin(math.radians(lat2)) -
         math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.cos(dlon))
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _turn_deg(heading, bearing):
    return abs((bearing - heading + 180) % 360 - 180)


# ── Severity weight profiles ────────────────────────────────────────────────
# Weights are normalized by their sum at scoring time, so profiles don't need
# to add up to 1.0, and registering a new factor (which appends a default
# weight to every profile) never breaks an existing one.
_DEFAULT_SEVERITY_WEIGHTS = {
    "LOW": {
        "reachability": 0.15, "turn": 0.10, "capability": 0.40,
        "fuel_margin": 0.15, "availability": 0.20,
    },
    "MODERATE": {
        "reachability": 0.30, "turn": 0.20, "capability": 0.25,
        "fuel_margin": 0.15, "availability": 0.10,
    },
    "HIGH": {
        "reachability": 0.45, "turn": 0.30, "capability": 0.10,
        "fuel_margin": 0.10, "availability": 0.05,
    },
}

# ── Emergency condition -> reachability modifiers ───────────────────────────
# Keyed by the same alert IDs the AlertTracker / math modules already raise
# (see modules/assessment/aircraft_health.py's _ALERT_SUBSYSTEM table), so
# wiring in a *new* AI-detected condition is just adding an entry here —
# nothing else in the selector needs to change.
_DEFAULT_CONDITION_MODIFIERS = {
    "FUEL_LEAK": {
        "fuel_burn_multiplier": 2.0,   # worst case: assume the leak rate doubles before landing
        "fuel_margin_buffer":   1.15,
    },
    "FUEL_IMBALANCE":  {"fuel_burn_multiplier": 1.05},
    "FUEL_EXHAUSTION": {"fuel_burn_multiplier": 1.10, "fuel_margin_buffer": 1.25},
    "MIN_DIVERT_FUEL": {"fuel_margin_buffer": 1.20},
    "ENGINE_FAILURE":  {"fuel_burn_multiplier": 1.10, "prefer_larger_airport": True},
    "THRUST_ASYM":     {"fuel_burn_multiplier": 1.05},
    "ICE_ACCUM":       {"fuel_burn_multiplier": 1.20},  # added drag/weight from airframe ice
    # Illustrative plug-in point: no live alert raises this yet, but a future
    # flight-control / AI module can register under whatever ID it uses (e.g.
    # "RUDDER_FAILURE") — the moment that ID shows up in active_conditions,
    # turns get capped with zero changes to the scoring code below.
    "DIRECTIONAL_CONTROL_LOSS": {"max_turn_deg": 45.0, "prefer_larger_airport": True},
}

_TURN_FUEL_PENALTY_FRAC = 0.05   # a full 180 deg turn adds ~5% to effective diversion distance
_DISTANCE_DECAY_NM      = 150.0  # exponential falloff scale for the reachability factor

# Below this fraction of required runway length, hard-exclude — always, at
# every relaxation tier. This is not a safety *margin* like the fuel/turn
# numbers below; it's the physical fact of whether the aircraft can stop on
# the pavement. Loosening it under pressure is exactly the failure mode this
# algorithm exists to prevent, so it's treated the same as a NOTAM closure:
# never touched by _RELAXATION_LEVELS below.
_MIN_LANDABLE_RATIO = 0.55

# ── Graceful degradation (the "G" in GRACE) ─────────────────────────────────
# If nothing survives the hard filters at full strictness (the worst-case
# fuel-burn/turn assumptions from the active conditions), the selector
# doesn't just return an empty list — it retries at progressively looser
# tiers. 1.0 = the full worst-case modifiers computed from active_conditions;
# 0.0 = baseline reachability (normal ICAO reserve, unrestricted turn). It
# never goes below "you must be able to reach it with a standard reserve
# intact". Runway length (_MIN_LANDABLE_RATIO, above) and NOTAM closures are
# never relaxed at any tier — those aren't margins, they're facts about
# whether landing is physically possible at all.
_RELAXATION_LEVELS = (1.0, 0.7, 0.4, 0.15, 0.0)


def _lerp(floor, full, relax):
    """relax=1.0 -> full (strict); relax=0.0 -> floor (lenient)."""
    return floor + (full - floor) * relax


def _relaxation_notes(relax, configured, effective):
    if relax >= 1.0:
        return []
    notes = []
    if effective["max_turn_deg"] > configured["max_turn_deg"] + 0.5:
        notes.append(
            f"turn limit relaxed {configured['max_turn_deg']:.0f}° → {effective['max_turn_deg']:.0f}°"
        )
    if effective["fuel_burn_multiplier"] < configured["fuel_burn_multiplier"] - 0.01:
        notes.append(
            f"worst-case fuel burn relaxed {configured['fuel_burn_multiplier']:.2f}x → {effective['fuel_burn_multiplier']:.2f}x"
        )
    if effective["fuel_margin_buffer"] < configured["fuel_margin_buffer"] - 0.01:
        notes.append(
            f"fuel reserve buffer relaxed {configured['fuel_margin_buffer']:.2f}x → {effective['fuel_margin_buffer']:.2f}x"
        )
    return notes


def _required_runway_ft(is_transport: bool, vref_kts: float) -> float:
    """Rough required-landing-distance heuristic. Approximate on purpose —
    swap in a real performance-model lookup later without touching callers."""
    if not is_transport:
        return 2200.0
    if vref_kts <= 0:
        return 6000.0
    return _clamp(vref_kts * 30.0, 4500.0, 9500.0)


def _resolve_modifiers(active_conditions, registry):
    max_turn_deg = 180.0
    fuel_burn_multiplier = 1.0
    fuel_margin_buffer = 1.0
    prefer_larger_airport = False
    for cond in active_conditions:
        mods = registry.get(cond)
        if not mods:
            continue
        if "max_turn_deg" in mods:
            max_turn_deg = min(max_turn_deg, mods["max_turn_deg"])
        if "fuel_burn_multiplier" in mods:
            fuel_burn_multiplier = max(fuel_burn_multiplier, mods["fuel_burn_multiplier"])
        if "fuel_margin_buffer" in mods:
            fuel_margin_buffer = max(fuel_margin_buffer, mods["fuel_margin_buffer"])
        if mods.get("prefer_larger_airport"):
            prefer_larger_airport = True
    return max_turn_deg, fuel_burn_multiplier, fuel_margin_buffer, prefer_larger_airport


def _closure_status(apt, notam_closed_airports, notam_closed_runways):
    """Mirrors the AERIS_UI SimulateMap closureState() rule: a runway closure
    only matters if there's no secondary runway to fall back to."""
    icao = apt.get("icao", "")
    closed_rwy_ids = notam_closed_runways.get(icao, [])
    runways = apt.get("runways") or []
    has_secondary = len(runways) > 1
    fully_closed = icao in notam_closed_airports or (bool(closed_rwy_ids) and not has_secondary)
    return fully_closed, closed_rwy_ids


def _capability_ft(apt, closed_rwy_ids):
    """Best usable runway length. Falls back to the authoritative
    max_rwy_ft classification field when per-runway end geometry wasn't
    joinable (NASR join gaps) or no closure applies to the known runways."""
    runways = apt.get("runways") or []
    if closed_rwy_ids and runways:
        open_lengths = [r.get("length_ft", 0) for r in runways if r.get("id") not in closed_rwy_ids]
        if open_lengths:
            return max(open_lengths)
        return 0.0
    return apt.get("max_rwy_ft", 0.0) or 0.0


# ── built-in factors: fn(candidate: dict, ctx: dict) -> float in [0, 1] ─────

def _factor_reachability(candidate, ctx):
    return math.exp(-candidate["distance_nm"] / _DISTANCE_DECAY_NM)


def _factor_turn(candidate, ctx):
    max_turn = max(ctx["max_turn_deg"], 1e-6)
    return 1.0 - min(candidate["turn_deg"], max_turn) / max_turn


def _factor_capability(candidate, ctx):
    required = max(ctx["required_rwy_ft"], 1.0)
    score = _clamp((candidate["best_runway_ft"] / required) / 1.5, 0.0, 1.0)
    if ctx["prefer_larger_airport"] and candidate["airport_type"] in ("large_airport", "medium_airport"):
        score = _clamp(score + 0.15, 0.0, 1.0)
    return score


def _factor_fuel_margin(candidate, ctx):
    if ctx["fuel_total_lbs"] <= 0:
        return 0.0
    return _clamp(candidate["fuel_margin_lbs"] / ctx["fuel_total_lbs"], 0.0, 1.0)


def _factor_availability(candidate, ctx):
    return 0.5 if candidate["runway_closures"] else 1.0


class DiversionSelector:
    """Reference implementation of GRACE — the Graceful-Relaxation Algorithm
    for aircraft Emergency Navigation.

    Not tied to any specific aircraft-state shape beyond a handful of dict
    keys (see select()) and doesn't touch the DataBus/FlightGenerator at
    all — decision/decision_engine.py is what wires this into the live sim.
    """

    def __init__(self):
        self._severity_weights = deepcopy(_DEFAULT_SEVERITY_WEIGHTS)
        self._condition_modifiers = deepcopy(_DEFAULT_CONDITION_MODIFIERS)
        self._factors = {}
        self._register_default_factors()

    # ── extension points ─────────────────────────────────────────────────

    def register_factor(self, name: str, fn, default_weight: float = 0.1) -> None:
        """Add a scoring factor: fn(candidate, ctx) -> float in [0, 1].

        Automatically appended to every existing severity weight profile at
        `default_weight` so it participates immediately; follow up with
        set_severity_weights() to fine-tune its weight per tier.
        """
        self._factors[name] = fn
        for weights in self._severity_weights.values():
            weights.setdefault(name, default_weight)

    def set_severity_weights(self, severity: str, weights: dict) -> None:
        self._severity_weights[severity.upper()] = dict(weights)

    def register_condition_modifier(self, condition: str, modifiers: dict, merge: bool = True) -> None:
        if merge and condition in self._condition_modifiers:
            self._condition_modifiers[condition] = {**self._condition_modifiers[condition], **modifiers}
        else:
            self._condition_modifiers[condition] = dict(modifiers)

    # ── main entry point ─────────────────────────────────────────────────

    def select(
        self,
        state: dict,
        airports: list,
        *,
        is_transport: bool = True,
        vref_kts: float = 0.0,
        severity: str | None = None,
        active_conditions: list | None = None,
        top_n: int = 3,
    ) -> list:
        severity = (severity or "MODERATE").upper()
        if severity not in self._severity_weights:
            severity = "MODERATE"
        active_conditions = active_conditions or []

        max_turn_deg, fuel_burn_mult, fuel_margin_buf, prefer_larger = _resolve_modifiers(
            active_conditions, self._condition_modifiers
        )
        configured = {
            "max_turn_deg":         max_turn_deg,
            "fuel_burn_multiplier": fuel_burn_mult,
            "fuel_margin_buffer":   fuel_margin_buf,
        }

        base_ctx = {
            "lat":                   state.get("lat", 0.0),
            "lon":                   state.get("lon", 0.0),
            "heading_deg":           state.get("heading_deg", state.get("track_deg", 0.0)),
            "groundspeed_kts":       max(state.get("groundspeed_kts", 0.0), 1.0),
            "fuel_total_lbs":        state.get("fuel_total_lbs", 0.0),
            "fuel_flow_lbs_hr":      max(state.get("fuel_flow_total_lbs_hr", 0.0), 0.0),
            "fuel_reserve_min_lbs":  state.get("fuel_reserve_min_lbs", 0.0),
            "required_rwy_ft":       _required_runway_ft(is_transport, vref_kts),
            # Fixed, never relaxed — see the module-level note on
            # _MIN_LANDABLE_RATIO. Landing distance is a physical limit, not
            # a safety margin to trade away when the algorithm is struggling.
            "min_landable_ratio":    _MIN_LANDABLE_RATIO,
            "prefer_larger_airport": prefer_larger,
            "notam_closed_airports": set(state.get("notam_closed_airports") or []),
            "notam_closed_runways":  state.get("notam_closed_runways") or {},
        }

        weights = self._severity_weights[severity]
        weight_total = sum(weights.values()) or 1.0

        # Try full strictness first; if nothing survives, retry progressively
        # looser tiers. NOTAM closures, runway length, and severity/weights
        # never change — only the fuel and turn safety margins loosen.
        for relax in _RELAXATION_LEVELS:
            ctx = dict(base_ctx)
            ctx["max_turn_deg"]         = _lerp(180.0, max_turn_deg, relax)
            ctx["fuel_burn_multiplier"] = _lerp(1.0, fuel_burn_mult, relax)
            ctx["fuel_margin_buffer"]   = _lerp(1.0, fuel_margin_buf, relax)

            results = []
            for apt in airports:
                cand = self._evaluate(apt, ctx)
                if cand is None:
                    continue
                score = sum(weights.get(f, 0.0) * cand["factors"].get(f, 0.0) for f in weights) / weight_total
                cand["score"] = round(score, 4)
                cand["severity"] = severity
                cand["relaxed"] = relax < 1.0
                cand["relaxation_notes"] = _relaxation_notes(relax, configured, ctx)
                results.append(cand)

            if results:
                results.sort(key=lambda c: c["score"], reverse=True)
                return results[:top_n]

        return []  # even fully relaxed, nothing is physically reachable

    # ── per-candidate evaluation ─────────────────────────────────────────

    def _evaluate(self, apt, ctx):
        lat, lon = apt.get("lat"), apt.get("lon")
        if lat is None or lon is None:
            return None

        fully_closed, closed_rwy_ids = _closure_status(
            apt, ctx["notam_closed_airports"], ctx["notam_closed_runways"]
        )
        if fully_closed:
            return None

        distance_nm = _haversine_nm(ctx["lat"], ctx["lon"], lat, lon)
        brg = _bearing_deg(ctx["lat"], ctx["lon"], lat, lon)
        turn = _turn_deg(ctx["heading_deg"], brg)

        if turn > ctx["max_turn_deg"]:
            return None  # can't physically turn far enough to get there

        # Turning costs fuel/time — inflate the effective diversion distance.
        turn_penalty = (turn / 180.0) * _TURN_FUEL_PENALTY_FRAC
        effective_distance_nm = distance_nm * (1.0 + turn_penalty)

        time_to_reach_hr = effective_distance_nm / ctx["groundspeed_kts"]
        burn_rate_lbs_hr = ctx["fuel_flow_lbs_hr"] * ctx["fuel_burn_multiplier"]
        fuel_required_lbs = burn_rate_lbs_hr * time_to_reach_hr
        reserve_required_lbs = ctx["fuel_reserve_min_lbs"] * ctx["fuel_margin_buffer"]

        fuel_margin_lbs = ctx["fuel_total_lbs"] - fuel_required_lbs - reserve_required_lbs
        if fuel_margin_lbs < 0:
            return None  # can't make it, worst case, with reserve intact

        best_rwy_ft = _capability_ft(apt, closed_rwy_ids)
        if best_rwy_ft < ctx["required_rwy_ft"] * ctx["min_landable_ratio"]:
            return None  # too short to land under any reasonable technique

        candidate = {
            "icao":            apt.get("icao"),
            "iata":            apt.get("iata"),
            "name":            apt.get("name"),
            "airport_type":    apt.get("airport_type"),
            "distance_nm":     round(distance_nm, 1),
            "bearing_deg":     round(brg, 1),
            "turn_deg":        round(turn, 1),
            "eta_min":         round(time_to_reach_hr * 60.0, 1),
            "best_runway_ft":  round(best_rwy_ft),
            "fuel_margin_lbs": round(fuel_margin_lbs, 1),
            "runway_closures": closed_rwy_ids,
        }

        factors = {name: _clamp(fn(candidate, ctx), 0.0, 1.0) for name, fn in self._factors.items()}
        candidate["factors"] = factors
        candidate["reason"] = self._explain(candidate)
        return candidate

    @staticmethod
    def _explain(candidate) -> str:
        bits = [f"{candidate['distance_nm']:.0f} nm", f"{candidate['turn_deg']:.0f}° turn"]
        if candidate["runway_closures"]:
            bits.append(f"alt RWY (closed: {', '.join(candidate['runway_closures'])})")
        bits.append(f"{candidate['fuel_margin_lbs']:.0f} lbs fuel margin at arrival")
        return ", ".join(bits)

    # ── built-in factor registration ─────────────────────────────────────

    def _register_default_factors(self) -> None:
        self.register_factor("reachability", _factor_reachability)
        self.register_factor("turn", _factor_turn)
        self.register_factor("capability", _factor_capability)
        self.register_factor("fuel_margin", _factor_fuel_margin)
        self.register_factor("availability", _factor_availability)


_default_instance: DiversionSelector | None = None


def default_selector() -> DiversionSelector:
    """Return the process-wide default GRACE instance (lazily constructed)."""
    global _default_instance
    if _default_instance is None:
        _default_instance = DiversionSelector()
    return _default_instance


def select_diversion_airports(state: dict, airports: list, **kwargs) -> list:
    """Run GRACE via the default instance. Convenience wrapper around
    default_selector().select(...) for callers that don't need custom
    factors/weights/condition modifiers."""
    return default_selector().select(state, airports, **kwargs)
