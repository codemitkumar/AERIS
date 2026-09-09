"""Full-distribution latency benchmark: mean/median/p95/p99/max over a
mixed workload of typical (single-tier) and stressed (multi-tier
relaxation) queries, matching real operational variance rather than two
hand-picked points.
"""
import os
import random
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "AERIS_ENGINE"))

from data.ingestion.faa_airport_loader import load_airports  # noqa: E402
from decision.diversion_selector import DiversionSelector  # noqa: E402

random.seed(11)
AIRPORTS = load_airports()


def random_state(tight):
    lat = random.uniform(30.0, 46.0)
    lon = random.uniform(-108.0, -82.0)
    heading = random.uniform(0.0, 360.0)
    if tight:
        fuel_total = random.uniform(1200.0, 3500.0)
        reserve = random.uniform(900.0, 1800.0)
        conditions = random.choice([["FUEL_LEAK"], ["DIRECTIONAL_CONTROL_LOSS"], ["FUEL_LEAK", "MIN_DIVERT_FUEL"]])
    else:
        fuel_total = random.uniform(6000.0, 25000.0)
        reserve = random.uniform(1500.0, 3500.0)
        conditions = []
    return {
        "lat": lat, "lon": lon, "heading_deg": heading, "track_deg": heading,
        "groundspeed_kts": random.uniform(140.0, 480.0),
        "fuel_total_lbs": fuel_total, "fuel_flow_total_lbs_hr": random.uniform(3000.0, 6000.0),
        "fuel_reserve_min_lbs": reserve, "time": random.uniform(600.0, 7200.0),
    }, random.choice(["LOW", "MODERATE", "HIGH"]), conditions


def percentile(sorted_vals, p):
    idx = min(len(sorted_vals) - 1, int(round(p / 100.0 * (len(sorted_vals) - 1))))
    return sorted_vals[idx]


def main():
    selector = DiversionSelector()
    N = 400
    batch = [random_state(tight=random.random() < 0.35) for _ in range(N)]

    # warm-up
    for state, sev, cond in batch[:10]:
        selector.select(state, AIRPORTS, is_transport=True, vref_kts=140.0,
                         severity=sev, active_conditions=cond, top_n=3)

    times_ms = []
    tiers_used = []
    for state, sev, cond in batch:
        t0 = time.perf_counter()
        res = selector.select(state, AIRPORTS, is_transport=True, vref_kts=140.0,
                               severity=sev, active_conditions=cond, top_n=3)
        times_ms.append((time.perf_counter() - t0) * 1000.0)
        tiers_used.append(res[0].get("relaxed") if res else "no-result")

    times_ms.sort()
    mean_ms = sum(times_ms) / len(times_ms)
    print(f"N={N} queries ({sum(1 for _,_,c in batch if c)} with active conditions)")
    print(f"mean   : {mean_ms:.2f} ms")
    print(f"median : {percentile(times_ms, 50):.2f} ms")
    print(f"p95    : {percentile(times_ms, 95):.2f} ms")
    print(f"p99    : {percentile(times_ms, 99):.2f} ms")
    print(f"max    : {times_ms[-1]:.2f} ms")
    print(f"min    : {times_ms[0]:.2f} ms")

    relaxed_count = sum(1 for t in tiers_used if t is True)
    noresult_count = sum(1 for t in tiers_used if t == "no-result")
    print(f"relaxed (>1 tier needed): {relaxed_count}/{N}")
    print(f"no result: {noresult_count}/{N}")


if __name__ == "__main__":
    main()
