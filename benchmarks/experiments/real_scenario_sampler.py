"""Builds ad-hoc, exploratory aircraft-state scenarios from real EUROCONTROL
flight-plan data, for demos/exploration outside the paper's evaluation.

NOT used by scenarios.py, run_benchmark.py, run_sensitivity.py, or
run_comparison.py -- those stay pinned to the paper's published numbers
(fixed hand-specified scenarios, FAA-only airport set, fixed seeds). This
script is a separate, additive utility.

Position/kinematics are derived from real flight plans (real ADEP/ADES,
real distance flown, real requested flight level) by interpolating a point
along the great-circle leg and estimating groundspeed/fuel from distance
and requested FL -- these derived numbers are reasonable approximations,
not measured quantities, and are labeled as such in the output.

GRACE eligibility note: sampled positions are only run against the FAA NASR
airport set (the only region with verified runway data end-to-end), so
GRACE naturally reports noReachableAirport for scenarios outside FAA
coverage -- that's the correct, honest behavior, not a bug. European
airports (eurocontrol_airport_loader) are NOT fed to GRACE here since they
carry no verified runway geometry (see that module's docstring).
"""
import math
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "AERIS_ENGINE"))

from data.ingestion.eurocontrol_loader import DEFAULT_MONTH, load_flights  # noqa: E402
from data.ingestion.faa_airport_loader import load_airports  # noqa: E402
from decision.diversion_selector import DiversionSelector  # noqa: E402

_EARTH_R_NM = 3440.065


def _bearing(lat1, lon1, lat2, lon2):
    dlon = math.radians(lon2 - lon1)
    y = math.sin(dlon) * math.cos(math.radians(lat2))
    x = (math.cos(math.radians(lat1)) * math.sin(math.radians(lat2)) -
         math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.cos(dlon))
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _interpolate(lat1, lon1, lat2, lon2, frac):
    """Point a fraction of the way along the great-circle leg (slerp)."""
    phi1, lam1 = math.radians(lat1), math.radians(lon1)
    phi2, lam2 = math.radians(lat2), math.radians(lon2)
    d = 2 * math.asin(math.sqrt(
        math.sin((phi2 - phi1) / 2) ** 2 +
        math.cos(phi1) * math.cos(phi2) * math.sin((lam2 - lam1) / 2) ** 2
    ))
    if d < 1e-9:
        return lat1, lon1
    a = math.sin((1 - frac) * d) / math.sin(d)
    b = math.sin(frac * d) / math.sin(d)
    x = a * math.cos(phi1) * math.cos(lam1) + b * math.cos(phi2) * math.cos(lam2)
    y = a * math.cos(phi1) * math.sin(lam1) + b * math.cos(phi2) * math.sin(lam2)
    z = a * math.sin(phi1) + b * math.sin(phi2)
    lat = math.degrees(math.atan2(z, math.sqrt(x * x + y * y)))
    lon = math.degrees(math.atan2(y, x))
    return lat, lon


def sample_scenarios(n=10, month=DEFAULT_MONTH, seed=None):
    """Return `n` scenario dicts (same {description, state, severity,
    active_conditions, reference} shape as scenarios.SCENARIOS' values)
    built from real flight plans for the given sample month.

    `state` positions the aircraft at a random point en route (10-90% along
    the real ADEP->ADES leg), heading toward ADES. `reference` is always
    None -- these aren't constructed to exercise a specific mechanism like
    the paper's S1-S7, so there's no crisp expected top-1 pick.
    """
    rng = random.Random(seed)
    flights = load_flights(month)
    if not flights:
        return []

    chosen = rng.sample(flights, min(n, len(flights)))
    scenarios = {}
    for fl in chosen:
        frac = rng.uniform(0.1, 0.9)
        lat, lon = _interpolate(
            fl["adep_lat"], fl["adep_lon"], fl["ades_lat"], fl["ades_lon"], frac
        )
        track = _bearing(lat, lon, fl["ades_lat"], fl["ades_lon"])

        fl_level = fl["requested_fl"] or 300.0
        # Rough true-airspeed-by-altitude relationship (approximation, not
        # a measured value): higher requested FL implies faster cruise.
        groundspeed_kts = max(150.0, min(500.0, 220.0 + fl_level * 0.6))

        dist_nm = fl["distance_flown_nm"] or 500.0
        remaining_nm = dist_nm * (1 - frac)
        # Fuel scaled from remaining real distance at a representative
        # burn rate -- an approximation for scenario realism, not a
        # measured fuel figure.
        fuel_flow_lbs_hr = 5000.0
        hours_remaining = remaining_nm / max(groundspeed_kts, 1.0)
        fuel_total_lbs = fuel_flow_lbs_hr * (hours_remaining + 1.0)

        key = f"REAL_{fl['ectl_id']}"
        scenarios[key] = {
            "description": (
                f"Real EUROCONTROL flight plan {fl['adep']}->{fl['ades']} "
                f"({fl['ac_type']}, {month}), sampled en route at {frac:.0%} "
                f"of {dist_nm:.0f} nm real distance flown."
            ),
            "state": {
                "lat": round(lat, 5), "lon": round(lon, 5),
                "heading_deg": round(track, 1), "track_deg": round(track, 1),
                "groundspeed_kts": round(groundspeed_kts, 1),
                "fuel_total_lbs": round(fuel_total_lbs, 1),
                "fuel_flow_total_lbs_hr": fuel_flow_lbs_hr,
                "fuel_reserve_min_lbs": 3000.0,
                "time": 3600.0,
            },
            "severity": "LOW",
            "active_conditions": [],
            "reference": None,
            "source_flight": {
                "adep": fl["adep"], "ades": fl["ades"], "ac_type": fl["ac_type"],
                "ac_operator": fl["ac_operator"], "requested_fl": fl["requested_fl"],
                "distance_flown_nm": fl["distance_flown_nm"],
            },
        }

    return scenarios


def main():
    scenarios = sample_scenarios(n=10, seed=7)
    airports = load_airports()
    selector = DiversionSelector()

    print(f"Sampled {len(scenarios)} real EUROCONTROL flight-plan scenarios "
          f"({DEFAULT_MONTH})\n")
    for key, sc in scenarios.items():
        result = selector.select(
            sc["state"], airports,
            is_transport=True, vref_kts=140.0,
            severity=sc["severity"], active_conditions=sc["active_conditions"],
            top_n=3,
        )
        top1 = result[0]["icao"] if result else "None (outside FAA airport coverage, expected for non-CONUS positions)"
        print(f"{key}: {sc['description']}")
        print(f"  GRACE top-1 (FAA airports only): {top1}\n")


if __name__ == "__main__":
    main()
