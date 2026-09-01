"""GRACE — Graceful-Relaxation Algorithm for Aircraft Emergency Navigation.

Ranks every reachable airport against live flight state + severity tier,
returns the top N diversion candidates.

Extension points:
    New scoring factor:       DiversionSelector.register_factor(...)
    New severity weight set:  DiversionSelector.set_severity_weights(...)
    New emergency condition:  DiversionSelector.register_condition_modifier(...)

Severity/active_conditions come from decision/decision_engine.py (today:
AircraftHealthModule's overallRisk; later, an AI classifier) — swapping that
out doesn't require touching this file.
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


# Weights normalize by their sum, so a new factor (Section register_factor)
# never breaks an existing profile.
# familiarity = origin (early flight) or destination (go-around); weighted
# higher at HIGH severity since there's less time to deliberate.
_DEFAULT_SEVERITY_WEIGHTS = {
    "LOW": {
        "reachability": 0.15, "turn": 0.10, "capability": 0.40,
        "fuel_margin": 0.15, "availability": 0.20, "familiarity": 0.10,
    },
    "MODERATE": {
        "reachability": 0.30, "turn": 0.20, "capability": 0.25,
        "fuel_margin": 0.15, "availability": 0.10, "familiarity": 0.15,
    },
    "HIGH": {
        "reachability": 0.45, "turn": 0.30, "capability": 0.10,
        "fuel_margin": 0.10, "availability": 0.05, "familiarity": 0.20,
    },
}

# Keyed by the same alert IDs AlertTracker already raises — a new AI-detected
# condition just needs an entry here, nothing else changes.
_DEFAULT_CONDITION_MODIFIERS = {
    "FUEL_LEAK": {
        "fuel_burn_multiplier": 2.0,   # assume the leak rate could double before landing
        "fuel_margin_buffer":   1.15,
    },
    "FUEL_IMBALANCE":  {"fuel_burn_multiplier": 1.05},
    "FUEL_EXHAUSTION": {"fuel_burn_multiplier": 1.10, "fuel_margin_buffer": 1.25},
    "MIN_DIVERT_FUEL": {"fuel_margin_buffer": 1.20},
    "ENGINE_FAILURE":  {"fuel_burn_multiplier": 1.10, "prefer_larger_airport": True},
    "THRUST_ASYM":     {"fuel_burn_multiplier": 1.05},
    "ICE_ACCUM":       {"fuel_burn_multiplier": 1.20},  # drag/weight from airframe ice
    # No live alert raises this yet — example of the plug-in point for a
    # future flight-control failure detector.
    "DIRECTIONAL_CONTROL_LOSS": {"max_turn_deg": 45.0, "prefer_larger_airport": True},
}

_TURN_FUEL_PENALTY_FRAC = 0.05   # full 180 deg turn adds ~5% to effective distance
_DISTANCE_DECAY_NM      = 150.0  # falloff scale for the reachability factor

# Hard floor, never relaxed at any tier — physical fact, not a safety margin.
_MIN_LANDABLE_RATIO = 0.55

# Graceful degradation: if nothing survives at full strictness, retry looser
# tiers (1.0 = full worst-case, 0.0 = standard reserve / unrestricted turn).
# Runway length and NOTAM closures are excluded from relaxation entirely.
_RELAXATION_LEVELS = (1.0, 0.7, 0.4, 0.15, 0.0)

# Known-airport anchoring: distance/turn scoring alone undervalues returning
# to the departure airport early in the flight (it's usually behind you by
# then) and re-attempting the destination during a go-around (you're already
# there). Anchoring only discounts the turn used for scoring/fuel-penalty and
# feeds the familiarity factor — it never touches NOTAM/runway/fuel hard
# filters, so an anchored airport that's genuinely unusable is still excluded.
_EARLY_FLIGHT_WINDOW_S = 20 * 60  # origin anchor fades out over 20 min after departure
_ANCHOR_TURN_DISCOUNT  = 0.15     # an anchored turn counts as 15% of its real angle


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
    """Rough landing-distance heuristic — swap for a real perf lookup later."""
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
    """Matches AERIS_UI's closureState(): a runway closure only counts if
    there's no secondary runway to fall back to."""
    icao = apt.get("icao", "")
    closed_rwy_ids = notam_closed_runways.get(icao, [])
    runways = apt.get("runways") or []
    has_secondary = len(runways) > 1
    fully_closed = icao in notam_closed_airports or (bool(closed_rwy_ids) and not has_secondary)
    return fully_closed, closed_rwy_ids


def _capability_ft(apt, closed_rwy_ids):
    """Best usable runway length; falls back to max_rwy_ft when per-runway
    geometry wasn't joinable."""
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
    # turn_deg_scoring, not the true angle — anchored turns count for less.
    max_turn = max(ctx["max_turn_deg"], 1e-6)
    return 1.0 - min(candidate["turn_deg_scoring"], max_turn) / max_turn


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


def _factor_familiarity(candidate, ctx):
    return candidate["anchor_strength"]


class DiversionSelector:
    """Reference implementation of GRACE.

    Only touches a handful of dict keys (see select()) — no DataBus or
    FlightGenerator coupling. decision/decision_engine.py wires it in.
    """

    def __init__(self):
        self._severity_weights = deepcopy(_DEFAULT_SEVERITY_WEIGHTS)
        self._condition_modifiers = deepcopy(_DEFAULT_CONDITION_MODIFIERS)
        self._factors = {}
        self._register_default_factors()

    # ── extension points ─────────────────────────────────────────────────

    def register_factor(self, name: str, fn, default_weight: float = 0.1) -> None:
        """fn(candidate, ctx) -> float in [0, 1]. Auto-added to every
        severity profile at default_weight; tune per-tier with
        set_severity_weights() afterward."""
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
            "min_landable_ratio":    _MIN_LANDABLE_RATIO,  # fixed, never relaxed
            "prefer_larger_airport": prefer_larger,
            "notam_closed_airports": set(state.get("notam_closed_airports") or []),
            "notam_closed_runways":  state.get("notam_closed_runways") or {},
            "origin_icao":           state.get("origin_icao"),
            "destination_icao":      state.get("destination_icao"),
            "origin_strength":       _clamp(
                1.0 - state.get("time", 0.0) / _EARLY_FLIGHT_WINDOW_S, 0.0, 1.0
            ) if state.get("origin_icao") else 0.0,
            "go_around_strength": (
                1.0 if "GO_AROUND" in active_conditions and state.get("destination_icao") else 0.0
            ),
        }

        weights = self._severity_weights[severity]
        weight_total = sum(weights.values()) or 1.0

        # Full strictness first, then progressively looser fuel/turn tiers.
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

        return []  # even fully relaxed, nothing is reachable

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

        # Hard filter always uses the true angle — anchoring never bypasses it.
        if turn > ctx["max_turn_deg"]:
            return None  # can't physically turn far enough to get there

        icao = apt.get("icao")
        anchor_strength = 0.0
        anchor = None
        if icao and icao == ctx["origin_icao"] and ctx["origin_strength"] > anchor_strength:
            anchor_strength, anchor = ctx["origin_strength"], "origin"
        if icao and icao == ctx["destination_icao"] and ctx["go_around_strength"] > anchor_strength:
            anchor_strength, anchor = ctx["go_around_strength"], "go_around"

        turn_discount = 1.0 - anchor_strength * (1.0 - _ANCHOR_TURN_DISCOUNT)
        turn_scoring = turn * turn_discount

        # Turning costs fuel/time — inflate the effective diversion distance.
        turn_penalty = (turn_scoring / 180.0) * _TURN_FUEL_PENALTY_FRAC
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
            "icao":             icao,
            "iata":             apt.get("iata"),
            "name":             apt.get("name"),
            "airport_type":     apt.get("airport_type"),
            "distance_nm":      round(distance_nm, 1),
            "bearing_deg":      round(brg, 1),
            "turn_deg":         round(turn, 1),           # true angle
            "turn_deg_scoring": round(turn_scoring, 1),   # what counts against the score
            "eta_min":          round(time_to_reach_hr * 60.0, 1),
            "best_runway_ft":   round(best_rwy_ft),
            "fuel_margin_lbs":  round(fuel_margin_lbs, 1),
            "runway_closures":  closed_rwy_ids,
            "anchor":           anchor,            # "origin" | "go_around" | None
            "anchor_strength":  round(anchor_strength, 2),
        }

        factors = {name: _clamp(fn(candidate, ctx), 0.0, 1.0) for name, fn in self._factors.items()}
        candidate["factors"] = factors
        candidate["reason"] = self._explain(candidate)
        return candidate

    @staticmethod
    def _explain(candidate) -> str:
        bits = []
        if candidate["anchor"] == "origin":
            bits.append("departure airport — briefed return-to-field option")
        elif candidate["anchor"] == "go_around":
            bits.append("go-around field — already established, no reason to look elsewhere")
        bits.append(f"{candidate['distance_nm']:.0f} nm")
        bits.append(f"{candidate['turn_deg']:.0f}° turn")
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
        self.register_factor("familiarity", _factor_familiarity)


_default_instance: DiversionSelector | None = None


def default_selector() -> DiversionSelector:
    global _default_instance
    if _default_instance is None:
        _default_instance = DiversionSelector()
    return _default_instance


def select_diversion_airports(state: dict, airports: list, **kwargs) -> list:
    return default_selector().select(state, airports, **kwargs)
