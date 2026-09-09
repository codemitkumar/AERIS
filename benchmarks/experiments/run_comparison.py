"""Run GRACE and the three baselines across all seven scenarios and report
a comparison table plus summary metrics. All numbers printed are computed
directly from the reference AERIS_ENGINE implementation -- nothing here is
hand-entered.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "AERIS_ENGINE"))

from data.ingestion.faa_airport_loader import load_airports  # noqa: E402
from decision.diversion_selector import DiversionSelector  # noqa: E402

from baselines import BASELINES  # noqa: E402
from scenarios import SCENARIOS, DEFAULT_IS_TRANSPORT, DEFAULT_VREF_KTS  # noqa: E402


def run_grace(selector, state, severity, active_conditions, top_n=3):
    return selector.select(
        state, AIRPORTS,
        is_transport=DEFAULT_IS_TRANSPORT, vref_kts=DEFAULT_VREF_KTS,
        severity=severity, active_conditions=active_conditions, top_n=top_n,
    )


def top1_icao(results):
    return results[0]["icao"] if results else None


def check_reference(name, top1, reference):
    """Returns True/False/None (None = not applicable / no crisp criterion)."""
    if reference == "None (no feasible candidate exists)":
        return top1 is None
    if reference.startswith("no single correct answer"):
        return None
    if reference.startswith("large_airport or medium_airport"):
        return None  # checked separately via airport_type, see main()
    if reference.startswith("no candidate with negative"):
        return None  # checked separately via fuel-margin field, see main()
    if reference.startswith("any candidate satisfying"):
        return top1 is not None
    # otherwise: reference is a specific ICAO
    return top1 == reference


def main():
    global AIRPORTS
    AIRPORTS = load_airports()
    selector = DiversionSelector()

    rows = []
    for key, sc in SCENARIOS.items():
        state = sc["state"]
        severity = sc["severity"]
        conditions = sc["active_conditions"]

        grace_res = run_grace(selector, state, severity, conditions)
        row = {"scenario": key, "description": sc["description"], "reference": sc["reference"]}
        row["GRACE"] = grace_res

        for bname, bfn in BASELINES.items():
            row[bname] = bfn(
                state, AIRPORTS, is_transport=DEFAULT_IS_TRANSPORT, vref_kts=DEFAULT_VREF_KTS,
                active_conditions=conditions, top_n=3,
            )
        rows.append(row)

    # ---- print full detail ----
    for row in rows:
        print(f"=== {row['scenario']} === {row['description']}")
        print(f"    reference: {row['reference']}")
        for alg in ["GRACE"] + list(BASELINES.keys()):
            res = row[alg]
            if not res:
                print(f"    {alg:14s}: (no candidate)")
                continue
            top = res[0]
            extra = ""
            if alg == "GRACE":
                extra = f" relaxed={top.get('relaxed')} anchor={top.get('anchor')}"
            fm = top.get("fuel_margin_lbs", top.get("nominal_fuel_margin_lbs"))
            print(f"    {alg:14s}: #1={top['icao']:6s} dist={top['distance_nm']:6.1f}nm "
                  f"turn={top['turn_deg']:5.1f} fuel_margin={fm}{extra}")
        print()

    # ---- summary metrics ----
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)

    agreement_rows = [r for r in rows if not r["reference"].startswith("no single correct answer")]
    for alg in ["GRACE"] + list(BASELINES.keys()):
        hits, applicable = 0, 0
        for row in agreement_rows:
            top1 = top1_icao(row[alg])
            ref = row["reference"]
            if ref.startswith("large_airport or medium_airport"):
                applicable += 1
                res = row[alg]
                if res and res[0]["airport_type"] in ("large_airport", "medium_airport"):
                    hits += 1
                continue
            if ref.startswith("no candidate with negative"):
                applicable += 1
                res = row[alg]
                fm = None
                if res:
                    fm = res[0].get("fuel_margin_lbs", res[0].get("nominal_fuel_margin_lbs"))
                if fm is None or fm >= 0:
                    hits += 1
                continue
            verdict = check_reference(alg, top1, ref)
            if verdict is None:
                continue
            applicable += 1
            if verdict:
                hits += 1
        print(f"{alg:14s} top-1 criterion met: {hits}/{applicable}")

    print()
    # top-3 recall for the two scenarios with a single specific reference ICAO
    for row in rows:
        ref = row["reference"]
        if ref in ("KICT", "KHUT"):
            for alg in ["GRACE"] + list(BASELINES.keys()):
                icaos = [c["icao"] for c in row[alg]]
                print(f"{row['scenario']:20s} {alg:14s} top-3={icaos} "
                      f"recall={'Y' if ref in icaos else 'N'}")

    print()
    out_path = os.path.join(os.path.dirname(__file__), "comparison_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, default=str)
    print(f"Full results written to {out_path}")


if __name__ == "__main__":
    main()
