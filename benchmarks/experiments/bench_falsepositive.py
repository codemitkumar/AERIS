"""Spurious annunciations over complete normal flights.

The corpus carries no recorded emergencies, so every alert raised over a
routine flight is either a genuine exceedance the crew flew through or a false
positive. Counting them per flight, and attributing them to an alert id, is
what the retuning claim rests on.

Run:
    python benchmarks/experiments/bench_falsepositive.py --dir <folder of .mat>
"""

import argparse
import asyncio
import glob
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.abspath(os.path.join(HERE, "..", "..", "AERIS_ENGINE"))
sys.path.insert(0, ENGINE)

from core.alert_tracker import AlertTracker
from core.data_bus import DataBus                                  # noqa: E402
from data.ingestion.aircraft_performance import get_performance    # noqa: E402
from data.ingestion.dashlink_loader import load_flight             # noqa: E402
from modules.registry import register_all                          # noqa: E402


class Sink:
    def __init__(self):
        self.raised = []          # topic == "alert"
        self.t = 0.0

    async def broadcast_alert(self, payload):
        if payload.get("topic") == "alert":
            self.raised.append((round(self.t, 2), payload.get("id"),
                                payload.get("severity")))

    async def broadcast(self, data):
        pass

    async def broadcast_meta(self, data):
        pass


async def one_flight(path, perf):
    sink = Sink()
    bus = DataBus()
    register_all(bus, ws=AlertTracker(sink), perf=perf)
    gen = load_flight(path, perf, airports=[])

    ticks = 0
    while not gen.finished:
        state = gen.step()
        sink.t = state["time"]
        await bus.publish(state)
        ticks += 1

    return {
        "flight": os.path.basename(path),
        "duration_min": round(gen.flight.duration_s / 60.0, 1),
        "rate_hz": gen.flight.base_rate,
        "ticks": ticks,
        "alerts": len(sink.raised),
        "by_id": dict(Counter(a[1] for a in sink.raised).most_common()),
        "by_severity": dict(Counter(a[2] for a in sink.raised)),
    }


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--out", default=os.path.join(HERE, "falsepositive_results.json"))
    args = ap.parse_args()

    perf = get_performance("DASHLINK", airport_elev_ft=0.0)
    paths = sorted(glob.glob(os.path.join(args.dir, "**", "*.mat"), recursive=True))
    paths = paths[:args.limit]

    rows = []
    for p in paths:
        try:
            row = await one_flight(p, perf)
        except Exception as exc:                       # a corrupt file is data, not a crash
            print(f"  {os.path.basename(p):<28} SKIPPED ({exc})")
            continue
        rows.append(row)
        print(f"  {row['flight']:<28} {row['duration_min']:>6.1f} min  "
              f"{row['ticks']:>7} ticks  {row['alerts']:>5} alerts")

    total_alerts = sum(r["alerts"] for r in rows)
    total_hours = sum(r["duration_min"] for r in rows) / 60.0
    combined = Counter()
    for r in rows:
        combined.update(r["by_id"])

    print(f"\n{len(rows)} flights, {total_hours:.1f} flight hours, "
          f"{total_alerts} alerts raised")
    print(f"rate: {total_alerts / total_hours:.2f} alerts per flight hour")
    print("\nmost frequent alert ids:")
    for k, v in combined.most_common(12):
        print(f"   {k:<22} {v:>5}")

    out = {
        "flights": rows,
        "total_alerts": total_alerts,
        "total_flight_hours": round(total_hours, 2),
        "alerts_per_flight_hour": round(total_alerts / total_hours, 3),
        "combined_by_id": dict(combined.most_common()),
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"\nwritten -> {args.out}")


asyncio.run(main())
