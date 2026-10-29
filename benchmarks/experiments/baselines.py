"""Baseline diversion-selection algorithms, for comparison against GRACE.

Each baseline reuses GRACE's own geometry and constraint primitives
(haversine distance, bearing, turn angle, NOTAM closure check, runway
capability, required-runway heuristic) imported directly from
decision.diversion_selector, so the comparison isolates *ranking and
relaxation strategy* rather than measurement differences from
re-implementing the same geometry twice.

    nearest      -- rank by distance only; no fuel check at all; no
                    awareness of active emergency conditions.
    distance_turn -- distance + a fixed linear turn penalty; a nominal
                    (non-worst-case) fuel check; no condition awareness.
    fixed_mcda   -- GRACE's own five non-familiarity factors at a single
                    fixed (MODERATE-equivalent) weight profile; DOES apply
                    active-condition hard modifiers (turn budget, worst-case
                    fuel) at full strictness only -- no relaxation cascade,
                    no known-airport anchoring.

This lets the evaluation isolate each of GRACE's three additions:
  nearest -> distance_turn   : value of a (nominal) fuel check
  distance_turn -> fixed_mcda: value of condition-aware hard constraints
                                + multi-factor scoring
  fixed_mcda -> GRACE        : value of severity-adaptive weights,
                                relaxation, and anchoring
"""

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "AERIS_ENGINE"))

from decision.diversion_selector import (  # noqa: E402
    _capability_ft,
    _clamp,
    _closure_status,
    _bearing_deg,
    _haversine_nm,
    _required_runway_ft,
    _resolve_modifiers,
    _turn_deg,
    _DEFAULT_CONDITION_MODIFIERS,
)

_FIXED_WEIGHTS = {
    "reachability": 0.30, "turn": 0.20, "capability": 0.25,
    "fuel_margin": 0.15, "availability": 0.10,
}


def _build_ctx(state, is_transport, vref_kts):
    return {
        "lat": state["lat"], "lon": state["lon"],
        "heading_deg": state.get("heading_deg", state.get("track_deg", 0.0)),
        "groundspeed_kts": max(state.get("groundspeed_kts", 0.0), 1.0),
        "fuel_total_lbs": state.get("fuel_total_lbs", 0.0),
        "fuel_flow_lbs_hr": max(state.get("fuel_flow_total_lbs_hr", 0.0), 0.0),
        "fuel_reserve_min_lbs": state.get("fuel_reserve_min_lbs", 0.0),
        "required_rwy_ft": _required_runway_ft(is_transport, vref_kts),
        "notam_closed_airports": set(state.get("notam_closed_airports") or []),
        "notam_closed_runways": state.get("notam_closed_runways") or {},
    }


def _base_candidate(apt, ctx):
    """NOTAM + runway-floor hard filter (always enforced -- physical facts,
    shared across every algorithm compared, GRACE included) plus geometry
    and a fuel-margin figure computed at *nominal* (non-worst-case) burn,
    reported for every candidate regardless of whether a given baseline
    treats it as a hard filter."""
    lat, lon = apt.get("lat"), apt.get("lon")
    if lat is None or lon is None:
        return None
    fully_closed, closed_rwy_ids = _closure_status(
        apt, ctx["notam_closed_airports"], ctx["notam_closed_runways"]
    )
    if fully_closed:
        return None
    best_rwy_ft = _capability_ft(apt, closed_rwy_ids)
    if best_rwy_ft < ctx["required_rwy_ft"] * 0.55:
        return None

    distance_nm = _haversine_nm(ctx["lat"], ctx["lon"], lat, lon)
    brg = _bearing_deg(ctx["lat"], ctx["lon"], lat, lon)
    turn = _turn_deg(ctx["heading_deg"], brg)
    time_hr = distance_nm / ctx["groundspeed_kts"]
    nominal_fuel_required = ctx["fuel_flow_lbs_hr"] * time_hr
    nominal_fuel_margin = ctx["fuel_total_lbs"] - nominal_fuel_required - ctx["fuel_reserve_min_lbs"]

    return {
        "icao": apt.get("icao"),
        "airport_type": apt.get("airport_type"),
        "distance_nm": round(distance_nm, 1),
        "turn_deg": round(turn, 1),
        "best_runway_ft": round(best_rwy_ft),
        "nominal_fuel_margin_lbs": round(nominal_fuel_margin, 1),
    }


def nearest(state, airports, is_transport=True, vref_kts=140.0, top_n=3, **_):
    """Baseline 1: rank by distance only. No fuel check, no condition awareness."""
    ctx = _build_ctx(state, is_transport, vref_kts)
    cands = [c for c in (_base_candidate(a, ctx) for a in airports) if c is not None]
    cands.sort(key=lambda c: c["distance_nm"])
    return cands[:top_n]


def distance_turn(state, airports, is_transport=True, vref_kts=140.0, top_n=3,
                   turn_weight_nm_per_deg=0.5, **_):
    """Baseline 2: distance + fixed turn penalty. Nominal fuel check only;
    no condition awareness (does not know about an active emergency's
    worst-case fuel or turn-budget implications)."""
    ctx = _build_ctx(state, is_transport, vref_kts)
    cands = [c for c in (_base_candidate(a, ctx) for a in airports) if c is not None]
    cands = [c for c in cands if c["nominal_fuel_margin_lbs"] >= 0]
    for c in cands:
        c["score_raw"] = c["distance_nm"] + turn_weight_nm_per_deg * c["turn_deg"]
    cands.sort(key=lambda c: c["score_raw"])
    return cands[:top_n]


def fixed_mcda(state, airports, is_transport=True, vref_kts=140.0, top_n=3,
               active_conditions=None, **_):
    """Baseline 3: GRACE's own five non-familiarity factors, one fixed
    weight profile, no severity adaptation, no anchoring. DOES apply the
    same active-condition hard modifiers (turn budget, worst-case fuel
    burn/reserve) GRACE does, but at full strictness only -- no relaxation
    cascade, so an infeasible query returns an empty list rather than a
    degraded answer."""
    active_conditions = active_conditions or []
    # return_to_field is an anchoring rule, which this baseline doesn't use.
    max_turn_deg, fuel_burn_mult, fuel_margin_buf, prefer_larger, _ = _resolve_modifiers(
        active_conditions, _DEFAULT_CONDITION_MODIFIERS
    )
    ctx = _build_ctx(state, is_transport, vref_kts)

    cands = []
    for apt in airports:
        c = _base_candidate(apt, ctx)
        if c is None:
            continue
        if c["turn_deg"] > max_turn_deg:
            continue
        time_hr = c["distance_nm"] / ctx["groundspeed_kts"]
        fuel_required = ctx["fuel_flow_lbs_hr"] * fuel_burn_mult * time_hr
        reserve_required = ctx["fuel_reserve_min_lbs"] * fuel_margin_buf
        fuel_margin = ctx["fuel_total_lbs"] - fuel_required - reserve_required
        if fuel_margin < 0:
            continue
        c["fuel_margin_lbs"] = round(fuel_margin, 1)

        reach = math.exp(-c["distance_nm"] / 150.0)
        turn_f = 1.0 - min(c["turn_deg"], max_turn_deg) / max(max_turn_deg, 1e-6)
        required = max(ctx["required_rwy_ft"], 1.0)
        cap = _clamp((c["best_runway_ft"] / required) / 1.5, 0.0, 1.0)
        if prefer_larger and c["airport_type"] in ("large_airport", "medium_airport"):
            cap = _clamp(cap + 0.15, 0.0, 1.0)
        fuel_f = _clamp(fuel_margin / max(ctx["fuel_total_lbs"], 1.0), 0.0, 1.0)
        avail = 1.0  # NOTAM closures already hard-filtered in _base_candidate

        score = (
            _FIXED_WEIGHTS["reachability"] * reach +
            _FIXED_WEIGHTS["turn"] * turn_f +
            _FIXED_WEIGHTS["capability"] * cap +
            _FIXED_WEIGHTS["fuel_margin"] * fuel_f +
            _FIXED_WEIGHTS["availability"] * avail
        )
        c["score_raw"] = score
        cands.append(c)

    cands.sort(key=lambda c: c["score_raw"], reverse=True)
    return cands[:top_n]


BASELINES = {
    "Nearest": nearest,
    "Distance+Turn": distance_turn,
    "Fixed MCDA": fixed_mcda,
}
