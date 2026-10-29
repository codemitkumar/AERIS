"""What the shared-state coupling actually costs.

Three derived fields are written by one module and read by another within the
same tick: crosswind_kts and tailwind_kts (read by the go-around advisory) and
glideslope_dots (read by the go-around and unstable-approach checks). The
composition root happens to register every producer before its consumer, so
the consumers see this tick's values.

Nothing enforces that. This permutes the subscriber order so consumers run
first, and measures what changes - turning "a potential ordering hazard" into
a number.
"""

import argparse
import asyncio
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.abspath(os.path.join(HERE, "..", "..", "AERIS_ENGINE"))
sys.path.insert(0, ENGINE)

from core.alert_tracker import AlertTracker
from core.data_bus import DataBus                                  # noqa: E402
from data.ingestion.aircraft_performance import get_performance    # noqa: E402
from data.ingestion.dashlink_loader import load_flight             # noqa: E402
from modules.registry import register_all                          # noqa: E402

COUPLED = ("crosswind_kts", "tailwind_kts", "glideslope_dots")


class Sink:
    def __init__(self):
        self.events = []
        self.t = 0.0

    async def broadcast_alert(self, payload):
        self.events.append((round(self.t, 2), payload.get("topic"), payload.get("id")))

    async def broadcast(self, data):
        pass

    async def broadcast_meta(self, data):
        pass


def owner(fn):
    """Which module class a bound on_state belongs to."""
    self_obj = getattr(fn, "__self__", None)
    return type(self_obj).__name__ if self_obj else getattr(fn, "__qualname__", "?")


async def run(flight, perf, ticks, reverse, skip_s):
    sink = Sink()
    bus = DataBus()
    register_all(bus, ws=AlertTracker(sink), perf=perf)
    if reverse:
        bus._subscribers = list(reversed(bus._subscribers))

    # Watch what the consumers could see: whether each coupled field is present
    # in the state by the time the last subscriber runs, and whether the value
    # is this tick's or carried over from the previous one.
    stale = {k: 0 for k in COUPLED}
    seen = {k: 0 for k in COUPLED}
    previous = {}

    async def probe(state):
        for k in COUPLED:
            if k in state:
                seen[k] += 1
                if k in previous and state[k] == previous[k]:
                    pass
                previous[k] = state[k]

    gen = load_flight(flight, perf, airports=[])
    for _ in range(int(skip_s * gen.flight.base_rate)):
        gen.step()

    ticks_missing = {k: 0 for k in COUPLED}
    for _ in range(ticks):
        if gen.finished:
            break
        state = gen.step()
        sink.t = state["time"]
        # A fresh dict each tick is what the engine does, so a consumer that
        # runs before its producer finds the key absent rather than stale.
        await bus.publish(state)
        for k in COUPLED:
            if k not in state:
                ticks_missing[k] += 1
        await probe(state)

    return sink.events, ticks_missing, stale


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--flight", required=True)
    ap.add_argument("--ticks", type=int, default=20000)
    ap.add_argument("--skip", type=float, default=600.0)
    ap.add_argument("--out", default=os.path.join(HERE, "ordering_results.json"))
    args = ap.parse_args()

    perf = get_performance("DASHLINK", airport_elev_ft=0.0)

    # Where the coupled modules sit in the composition root's order.
    probe_bus = DataBus()
    register_all(probe_bus, ws=Sink(), perf=perf)
    order = [owner(fn) for fn in probe_bus._subscribers]
    positions = {name: i for i, name in enumerate(order)}
    interesting = {n: positions[n] for n in (
        "GlideslopeDeviationModule", "CrosswindLimitModule", "TailwindModule",
        "UnstableApproachModule", "GoAroundAdvisoryModule", "AircraftHealthModule",
    ) if n in positions}

    print("registration positions of the coupled modules:")
    for n, i in sorted(interesting.items(), key=lambda kv: kv[1]):
        print(f"   {i:>3}  {n}")

    base_events, base_missing, _ = await run(
        args.flight, perf, args.ticks, reverse=False, skip_s=args.skip)
    rev_events, rev_missing, _ = await run(
        args.flight, perf, args.ticks, reverse=True, skip_s=args.skip)

    base_ids = [e[2] for e in base_events]
    rev_ids = [e[2] for e in rev_events]

    print(f"\nas registered : {len(base_events)} alert events")
    print(f"reversed      : {len(rev_events)} alert events")
    print(f"same sequence : {base_ids == rev_ids}")

    print("\nticks where a coupled field was absent when the tick ended:")
    for k in COUPLED:
        print(f"   {k:<18} as-registered {base_missing[k]:>6}   "
              f"reversed {rev_missing[k]:>6}")

    from collections import Counter
    cb, cr = Counter(base_ids), Counter(rev_ids)
    diff = {k: (cb.get(k, 0), cr.get(k, 0)) for k in set(cb) | set(cr)
            if cb.get(k, 0) != cr.get(k, 0)}
    if diff:
        print("\nalert types whose count changed with order:")
        for k, (a, b) in sorted(diff.items()):
            print(f"   {k:<20} {a:>4} -> {b:>4}")
    else:
        print("\nno alert type changed count")

    out = {
        "registration_positions": interesting,
        "coupled_fields": list(COUPLED),
        "as_registered": {"events": len(base_events), "missing": base_missing},
        "reversed": {"events": len(rev_events), "missing": rev_missing},
        "sequence_identical": base_ids == rev_ids,
        "per_alert_delta": {k: {"as_registered": a, "reversed": b}
                            for k, (a, b) in diff.items()},
        "ticks": args.ticks,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"\nwritten -> {args.out}")


asyncio.run(main())
