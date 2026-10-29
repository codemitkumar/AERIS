"""Is the bus deterministic by design, or only because nothing yields?

The fan-out uses asyncio.gather on one event loop, so a handler runs
uninterrupted until its first await. Eleven handlers await and then write
derived values into the shared state. Whether that matters depends entirely on
whether the await actually suspends:

  - a sink whose broadcast_alert returns without awaiting never yields, so the
    handler runs start to finish and ordering is fixed
  - a real WebSocket send does yield, and at that point another module can run
    and observe the state before those writes have landed

This replays one flight under both sinks and compares what the modules saw.
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

# Values written into the shared state by one module for others to read. If the
# order modules observe these in shifts between runs, the bus is not
# deterministic under a yielding sink.
DERIVED = ("density_alt_ft", "isa_dev_c", "glide_range_nm",
           "endurance_min", "ice_accretion_mm", "assessment")


class Sink:
    def __init__(self, yields: bool):
        self.yields = yields
        self.events = []
        self.t = 0.0

    async def broadcast_alert(self, payload):
        self.events.append((round(self.t, 3), payload.get("topic"), payload.get("id")))
        if self.yields:
            # What a real socket write does: hand control back to the loop.
            await asyncio.sleep(0)

    async def broadcast(self, data):
        if self.yields:
            await asyncio.sleep(0)

    async def broadcast_meta(self, data):
        pass


class Observer:
    """Subscribes last and records which derived keys exist by the time it runs."""

    def __init__(self):
        self.seen = []

    async def on_state(self, state):
        self.seen.append(tuple(k for k in DERIVED if k in state))


async def run_once(flight, perf, ticks, yields, skip_s=600.0):
    sink = Sink(yields)
    bus = DataBus()
    register_all(bus, ws=AlertTracker(sink), perf=perf)
    obs = Observer()
    bus.subscribe(obs.on_state)

    gen = load_flight(flight, perf, airports=[])
    for _ in range(int(skip_s * gen.flight.base_rate)):
        gen.step()
    for _ in range(ticks):
        if gen.finished:
            break
        state = gen.step()
        sink.t = state["time"]
        await bus.publish(state)
    return sink.events, obs.seen


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--flight", required=True)
    ap.add_argument("--ticks", type=int, default=3000)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--out", default=os.path.join(HERE, "interleave_results.json"))
    args = ap.parse_args()

    perf = get_performance("DASHLINK", airport_elev_ft=0.0)
    out = {}

    for label, yields in (("non_yielding_sink", False), ("yielding_sink", True)):
        alert_traces, view_traces = [], []
        for _ in range(args.runs):
            a, v = await run_once(args.flight, perf, args.ticks, yields)
            alert_traces.append(a)
            view_traces.append(v)

        alerts_identical = all(t == alert_traces[0] for t in alert_traces[1:])
        views_identical = all(t == view_traces[0] for t in view_traces[1:])
        # How often the last subscriber ran before the derived values it would
        # need had been written.
        incomplete = sum(1 for v in view_traces[0] if len(v) < len(DERIVED))

        out[label] = {
            "runs": args.runs,
            "alert_events": [len(t) for t in alert_traces],
            "alert_sequence_identical": alerts_identical,
            "observed_state_identical": views_identical,
            "ticks_where_derived_keys_missing": incomplete,
            "ticks_observed": len(view_traces[0]),
        }
        print(f"{label}:")
        print(f"   alert sequence identical across {args.runs} runs : {alerts_identical}")
        print(f"   observed derived state identical                : {views_identical}")
        print(f"   ticks missing >=1 derived key                   : "
              f"{incomplete}/{len(view_traces[0])}")

    same = (out["non_yielding_sink"]["alert_events"][0]
            == out["yielding_sink"]["alert_events"][0])
    out["alert_count_matches_between_sinks"] = same
    print(f"\nalert count identical between the two sinks: {same}")

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"written -> {args.out}")


asyncio.run(main())
