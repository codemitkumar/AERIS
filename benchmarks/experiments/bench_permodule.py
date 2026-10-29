"""Where the per-tick budget actually goes.

The first scaling attempt timed prefixes of the subscriber list and reported a
clean linear fit. That fit was an artifact: the aggregation stage was raising
on every tick and costing nothing. With it running, the population turns out
not to be homogeneous at all - fifty-four detection modules that cost a
microsecond or two each, and two downstream stages that cost orders of
magnitude more.

This times every subscriber separately so the distribution is visible rather
than averaged away, and counts how often the whole tick misses its deadline.
"""

import argparse
import asyncio
import json
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.abspath(os.path.join(HERE, "..", "..", "AERIS_ENGINE"))
sys.path.insert(0, ENGINE)

from core.alert_tracker import AlertTracker                        # noqa: E402
from core.data_bus import DataBus                                  # noqa: E402
from data.ingestion.aircraft_performance import get_performance    # noqa: E402
from data.ingestion.dashlink_loader import load_flight             # noqa: E402
from modules.registry import register_all                          # noqa: E402


class Sink:
    def __init__(self):
        self.n = 0

    async def broadcast_alert(self, payload):
        self.n += 1

    async def broadcast(self, data):
        pass

    async def broadcast_meta(self, data):
        pass


def name_of(fn):
    obj = getattr(fn, "__self__", None)
    return type(obj).__name__ if obj else getattr(fn, "__qualname__", "?")


class TimedBus(DataBus):
    """A bus that records how long each subscriber took, every tick."""

    def __init__(self):
        super().__init__()
        self.samples: dict[str, list] = {}

    async def publish(self, state):
        for fn in self._subscribers:
            key = name_of(fn)
            t0 = time.perf_counter()
            try:
                await fn(state)
            except Exception:
                pass
            self.samples.setdefault(key, []).append(
                (time.perf_counter() - t0) * 1e6)     # microseconds


def summarise(us):
    us = sorted(us)
    n = len(us)
    return {
        "mean_us": round(statistics.fmean(us), 2),
        "p50_us": round(us[n // 2], 2),
        "p99_us": round(us[int(n * 0.99)], 2),
        "max_us": round(us[-1], 2),
        "total_ms": round(sum(us) / 1000.0, 1),
    }


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--flight", required=True)
    ap.add_argument("--ticks", type=int, default=20000)
    ap.add_argument("--skip", type=float, default=600.0)
    ap.add_argument("--out", default=os.path.join(HERE, "permodule_results.json"))
    args = ap.parse_args()

    perf = get_performance("DASHLINK", airport_elev_ft=0.0)
    sink = Sink()
    bus = TimedBus()
    register_all(bus, ws=AlertTracker(sink), perf=perf)

    gen = load_flight(args.flight, perf, airports=[])
    budget_ms = 1000.0 / gen.flight.base_rate

    for _ in range(int(args.skip * gen.flight.base_rate)):
        gen.step()

    tick_ms = []
    for _ in range(args.ticks):
        if gen.finished:
            break
        t0 = time.perf_counter()
        await bus.publish(gen.step())
        tick_ms.append((time.perf_counter() - t0) * 1000.0)

    rows = [{"module": k, **summarise(v)} for k, v in bus.samples.items()]
    rows.sort(key=lambda r: r["mean_us"], reverse=True)

    detection = [r for r in rows
                 if r["module"] not in ("AircraftHealthModule", "DiversionDecisionModule")]
    downstream = [r for r in rows
                  if r["module"] in ("AircraftHealthModule", "DiversionDecisionModule")]

    det_mean = statistics.fmean(r["mean_us"] for r in detection)
    det_total = sum(r["mean_us"] for r in detection)
    down_total = sum(r["mean_us"] for r in downstream)

    over = sum(1 for t in tick_ms if t > budget_ms)
    st = sorted(tick_ms)

    print(f"flight {os.path.basename(args.flight)} | {len(tick_ms)} ticks | "
          f"budget {budget_ms:.2f} ms\n")

    print("ten most expensive subscribers (microseconds per tick):")
    print(f"   {'module':<30} {'mean':>9} {'p99':>9} {'max':>10}")
    for r in rows[:10]:
        print(f"   {r['module']:<30} {r['mean_us']:>9.2f} {r['p99_us']:>9.2f} "
              f"{r['max_us']:>10.2f}")

    print(f"\ndetection modules      : {len(detection)}")
    print(f"   mean cost each      : {det_mean:.2f} us")
    print(f"   combined per tick   : {det_total:.1f} us "
          f"({det_total / 1000 / budget_ms * 100:.2f}% of budget)")
    print(f"downstream stages      : {len(downstream)}")
    print(f"   combined per tick   : {down_total:.1f} us "
          f"({down_total / 1000 / budget_ms * 100:.2f}% of budget)")
    if det_total:
        print(f"   ratio to detection  : {down_total / det_total:.1f}x")

    print(f"\nwhole-tick latency     : p50 {st[len(st)//2]:.3f} ms  "
          f"p99 {st[int(len(st)*0.99)]:.3f} ms  max {st[-1]:.3f} ms")
    print(f"deadline misses        : {over}/{len(tick_ms)} "
          f"({over / len(tick_ms) * 100:.3f}%)")

    out = {
        "flight": os.path.basename(args.flight),
        "ticks": len(tick_ms),
        "budget_ms": round(budget_ms, 3),
        "per_module": rows,
        "detection_count": len(detection),
        "detection_mean_us": round(det_mean, 3),
        "detection_total_us": round(det_total, 2),
        "downstream_total_us": round(down_total, 2),
        "tick_p50_ms": round(st[len(st) // 2], 4),
        "tick_p99_ms": round(st[int(len(st) * 0.99)], 4),
        "tick_max_ms": round(st[-1], 4),
        "deadline_misses": over,
        "deadline_miss_pct": round(over / len(tick_ms) * 100, 4),
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"\nwritten -> {args.out}")


asyncio.run(main())
