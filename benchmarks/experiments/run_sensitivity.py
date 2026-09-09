"""Sensitivity analysis for GRACE's weight profiles and engineering
constants. All perturbations use GRACE's own public extension interface
(set_severity_weights) or direct module-constant patching (restored after
each test) -- no logic is duplicated or reimplemented here.
"""
import copy
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "AERIS_ENGINE"))

from data.ingestion.faa_airport_loader import load_airports  # noqa: E402
import decision.diversion_selector as ds  # noqa: E402
from decision.diversion_selector import DiversionSelector  # noqa: E402

random.seed(42)
AIRPORTS = load_airports()
CONDITION_POOL = [[], [], [], ["FUEL_LEAK"], ["ENGINE_FAILURE"], ["ICE_ACCUM"]]
SEVERITIES = ["LOW", "MODERATE", "HIGH"]


def random_state(tight=False):
    lat = random.uniform(30.0, 46.0)
    lon = random.uniform(-108.0, -82.0)
    heading = random.uniform(0.0, 360.0)
    gs = random.uniform(140.0, 480.0)
    if tight:
        fuel_total = random.uniform(1200.0, 3500.0)
        reserve = random.uniform(900.0, 1800.0)
        conditions = random.choice([["FUEL_LEAK"], ["DIRECTIONAL_CONTROL_LOSS"], ["FUEL_LEAK", "MIN_DIVERT_FUEL"]])
    else:
        fuel_total = random.uniform(6000.0, 25000.0)
        reserve = random.uniform(1500.0, 3500.0)
        conditions = random.choice(CONDITION_POOL)
    flow = random.uniform(3000.0, 6000.0)
    state = {
        "lat": lat, "lon": lon, "heading_deg": heading, "track_deg": heading,
        "groundspeed_kts": gs, "fuel_total_lbs": fuel_total,
        "fuel_flow_total_lbs_hr": flow, "fuel_reserve_min_lbs": reserve,
        "time": random.uniform(600.0, 7200.0),
    }
    severity = random.choice(SEVERITIES)
    return state, severity, conditions


def top1(selector, state, severity, conditions):
    res = selector.select(state, AIRPORTS, is_transport=True, vref_kts=140.0,
                           severity=severity, active_conditions=conditions, top_n=1)
    return res[0]["icao"] if res else None


# ---------------------------------------------------------------------------
# 1. Weight sensitivity: perturb reach/turn/fuel_margin by +-20%, one at a
#    time, in whichever severity profile the scenario drew.
# ---------------------------------------------------------------------------
def weight_sensitivity(n=150):
    batch = [random_state(tight=random.random() < 0.3) for _ in range(n)]
    base_selector = DiversionSelector()
    baseline = [top1(base_selector, s, sev, c) for s, sev, c in batch]

    results = {}
    for factor in ["reachability", "turn", "fuel_margin"]:
        for direction, mult in [("+20%", 1.2), ("-20%", 0.8)]:
            changed = 0
            evaluated = 0
            for (state, severity, conditions), base_pick in zip(batch, baseline):
                sel = DiversionSelector()
                weights = dict(sel._severity_weights[severity])
                weights[factor] = weights[factor] * mult
                sel.set_severity_weights(severity, weights)
                pick = top1(sel, state, severity, conditions)
                evaluated += 1
                if pick != base_pick:
                    changed += 1
            results[f"{factor} {direction}"] = (changed, evaluated)
    return results


# ---------------------------------------------------------------------------
# 2. Parameter sensitivity: rho_min (runway floor) and reachability decay,
#    swept on the same kind of general random batch.
# ---------------------------------------------------------------------------
def parameter_sensitivity(n=150):
    batch = [random_state(tight=random.random() < 0.3) for _ in range(n)]
    base_selector = DiversionSelector()
    baseline = [top1(base_selector, s, sev, c) for s, sev, c in batch]

    results = {}

    orig_ratio = ds._MIN_LANDABLE_RATIO
    for val in (0.50, 0.65, 0.80):
        ds._MIN_LANDABLE_RATIO = val
        changed = sum(
            1 for (state, sev, cond), base in zip(batch, baseline)
            if top1(DiversionSelector(), state, sev, cond) != base
        )
        results[f"rho_min={val}"] = (changed, n)
    ds._MIN_LANDABLE_RATIO = orig_ratio

    orig_decay = ds._DISTANCE_DECAY_NM
    for val in (50.0, 100.0, 300.0):
        ds._DISTANCE_DECAY_NM = val
        changed = sum(
            1 for (state, sev, cond), base in zip(batch, baseline)
            if top1(DiversionSelector(), state, sev, cond) != base
        )
        results[f"reach_decay={val}nm"] = (changed, n)
    ds._DISTANCE_DECAY_NM = orig_decay

    return results


# ---------------------------------------------------------------------------
# 3. Relaxation granularity: does the number of relaxation tiers change
#    whether a feasible candidate is found at all, on a fuel/turn-stressed
#    batch (as opposed to which candidate is found, which by construction
#    is dominated by the resolved (mu_f, mu_r, theta_max) at whichever tier
#    first succeeds, not by tier count itself)?
# ---------------------------------------------------------------------------
def relaxation_tier_sensitivity(n=100):
    batch = [random_state(tight=True) for _ in range(n)]
    orig_levels = ds._RELAXATION_LEVELS

    results = {}
    for levels in [
        (1.0, 0.0),
        (1.0, 0.5, 0.0),
        (1.0, 0.7, 0.4, 0.15, 0.0),
        (1.0, 0.85, 0.7, 0.55, 0.4, 0.25, 0.1, 0.0),
    ]:
        ds._RELAXATION_LEVELS = levels
        found = sum(1 for s, sev, c in batch if top1(DiversionSelector(), s, sev, c) is not None)
        results[f"{len(levels)} tiers"] = (found, n)
    ds._RELAXATION_LEVELS = orig_levels
    return results


# ---------------------------------------------------------------------------
# 4. T_early sensitivity: targeted early-departure-style batch.
# ---------------------------------------------------------------------------
def early_window_sensitivity(n=50):
    real_airports = [a for a in AIRPORTS if a.get("runways")]
    batch = []
    for _ in range(n):
        origin = random.choice(real_airports)
        elapsed_s = random.uniform(0, 2400)  # 0-40 min
        heading = random.uniform(0, 360)
        # position the aircraft a few nm from origin, roughly opposite the
        # bearing back to it (as if already established on an outbound course)
        from decision.diversion_selector import _bearing_deg, _haversine_nm
        # crude offset: move ~ (elapsed_s/60 * 4 nm/min) along `heading`
        dist_nm = (elapsed_s / 60.0) * 4.0 + 2.0
        dlat = (dist_nm / 60.0) * (1 if 0 <= heading < 180 else -1) * abs(1) * 0.7
        dlon = (dist_nm / 60.0) * (1 if 90 <= heading < 270 else -1) * 0.7
        state = {
            "lat": origin["lat"] + dlat, "lon": origin["lon"] + dlon,
            "heading_deg": heading, "track_deg": heading,
            "groundspeed_kts": random.uniform(150, 300),
            "fuel_total_lbs": random.uniform(8000, 20000),
            "fuel_flow_total_lbs_hr": random.uniform(3500, 5500),
            "fuel_reserve_min_lbs": random.uniform(1500, 3000),
            "time": elapsed_s, "origin_icao": origin["icao"],
        }
        batch.append((state, origin["icao"]))

    orig_window = ds._EARLY_FLIGHT_WINDOW_S
    results = {}
    for minutes in (5, 10, 20, 30, 40):
        ds._EARLY_FLIGHT_WINDOW_S = minutes * 60
        origin_picked = sum(
            1 for state, origin_icao in batch
            if top1(DiversionSelector(), state, "HIGH", []) == origin_icao
        )
        results[f"T_early={minutes}min"] = (origin_picked, n)
    ds._EARLY_FLIGHT_WINDOW_S = orig_window
    return results


if __name__ == "__main__":
    print("=== Weight sensitivity (+-20%, top-1 change rate) ===")
    for k, (changed, n) in weight_sensitivity().items():
        print(f"  {k:20s}: {changed}/{n} changed ({100*changed/n:.1f}%)")

    print()
    print("=== Parameter sensitivity (top-1 change rate vs default) ===")
    for k, (changed, n) in parameter_sensitivity().items():
        print(f"  {k:20s}: {changed}/{n} changed ({100*changed/n:.1f}%)")

    print()
    print("=== Relaxation tier granularity (feasible-candidate-found rate, stressed batch) ===")
    for k, (found, n) in relaxation_tier_sensitivity().items():
        print(f"  {k:20s}: {found}/{n} found ({100*found/n:.1f}%)")

    print()
    print("=== T_early window (origin-airport-picked rate, targeted early-departure batch) ===")
    for k, (picked, n) in early_window_sensitivity().items():
        print(f"  {k:20s}: {picked}/{n} ({100*picked/n:.1f}%)")
