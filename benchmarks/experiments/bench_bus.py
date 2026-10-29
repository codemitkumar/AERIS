"""Measurements for the architecture claims: cost, determinism, isolation.

Everything the paper asserts about scaling, latency and fault containment is
measured here against a real recorded flight rather than a synthetic loop, so
the numbers include whatever the actual channel mix and phase transitions do
to the modules.

Run:
    python benchmarks/experiments/bench_bus.py --flight <path.mat> [--out results.json]
"""

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
import tracemalloc

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.abspath(os.path.join(HERE, "..", "..", "AERIS_ENGINE"))
sys.path.insert(0, ENGINE)

from core.alert_tracker import AlertTracker
from core.data_bus import DataBus                       # noqa: E402
from data.ingestion.aircraft_performance import get_performance   # noqa: E402
from data.ingestion.dashlink_loader import load_flight  # noqa: E402
from modules.registry import register_all               # noqa: E402


class Sink:
    """Stands in for the WebSocket, recording alerts instead of sending them."""

    def __init__(self):
        self.events = []
        self.t = 0.0

    async def broadcast_alert(self, payload):
        self.events.append((round(self.t, 3), payload.get("topic"),
                            payload.get("id"), payload.get("severity")))

    async def broadcast(self, data):
        pass

    async def broadcast_meta(self, data):
        pass


def build(flight_path, perf):
    """A bus with every module attached, plus the sink that captured them."""
    sink = Sink()
    bus = DataBus()
    register_all(bus, ws=AlertTracker(sink), perf=perf)
    return bus, sink


def subset_bus(full_bus, n):
    """A bus carrying only the first n subscribers of a fully-populated one.

    Slicing the subscriber list rather than re-registering keeps each module's
    identity and internal state exactly as register_all built it, so the only
    variable between points is how many of them the bus fans out to.
    """
    bus = DataBus()
    bus._subscribers = list(full_bus._subscribers[:n])
    return bus


async def replay(gen, bus, sink, ticks):
    """Push `ticks` states through the bus, timing each publish."""
    per_tick = []
    for _ in range(ticks):
        if gen.finished:
            break
        state = gen.step()
        sink.t = state["time"]
        t0 = time.perf_counter()
        await bus.publish(state)
        per_tick.append((time.perf_counter() - t0) * 1000.0)   # ms
    return per_tick


def stats(samples):
    samples = sorted(samples)
    n = len(samples)
    return {
        "n": n,
        "mean_ms": round(statistics.fmean(samples), 4),
        "p50_ms": round(samples[n // 2], 4),
        "p95_ms": round(samples[int(n * 0.95)], 4),
        "p99_ms": round(samples[int(n * 0.99)], 4),
        "max_ms": round(samples[-1], 4),
    }


# ── experiment 1: does per-tick cost grow linearly with module count? ────────

async def exp_scaling(flight_path, perf, ticks, points):
    rows = []
    for n in points:
        gen = load_flight(flight_path, perf, airports=[])
        full, sink = build(flight_path, perf)
        bus = subset_bus(full, n)
        # Skip the ground roll so the sample lands in a phase where most
        # modules are actually doing work rather than returning early.
        for _ in range(int(600 * gen.flight.base_rate)):
            gen.step()
        per_tick = await replay(gen, bus, sink, ticks)
        row = {"modules": len(bus._subscribers), **stats(per_tick)}
        rows.append(row)
        print(f"  {row['modules']:>3} modules  mean {row['mean_ms']:.4f} ms  "
              f"p95 {row['p95_ms']:.4f}  p99 {row['p99_ms']:.4f}  max {row['max_ms']:.4f}")
    return rows


# ── experiment 2: is the concurrent fan-out actually deterministic? ──────────

async def exp_determinism(flight_path, perf, ticks, runs):
    """Replay the same flight repeatedly; the alert sequence must be identical.

    The bus fans out with asyncio.gather. If any module yielded control partway
    through its own read-modify-write of the shared state, another module could
    interleave and the resulting alert order would vary between runs.
    """
    traces = []
    for _ in range(runs):
        gen = load_flight(flight_path, perf, airports=[])
        bus, sink = build(flight_path, perf)
        for _ in range(int(600 * gen.flight.base_rate)):
            gen.step()
        await replay(gen, bus, sink, ticks)
        traces.append(sink.events)

    first = traces[0]
    identical = all(t == first for t in traces[1:])
    return {
        "runs": runs,
        "events_per_run": [len(t) for t in traces],
        "identical": identical,
        "events": len(first),
    }


def exp_await_audit():
    """Static check behind the determinism result.

    A module is only safe to run under gather if it reaches its first await
    after it has finished reading and writing the shared state. Anything that
    awaits earlier is a place where interleaving could bite, so count them
    rather than claiming none exist.
    """
    import ast
    import pathlib

    root = pathlib.Path(ENGINE) / "modules"
    total = suspect = 0
    offenders = []

    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef) or node.name != "on_state":
                continue
            total += 1
            body = node.body
            # Index of the last statement that writes to the state dict, and of
            # the first await anywhere in the handler.
            last_write = -1
            first_await = len(body)
            for i, stmt in enumerate(body):
                src = ast.dump(stmt)
                if "Subscript" in src and "state" in ast.unparse(stmt) and (
                        isinstance(stmt, (ast.Assign, ast.AugAssign))):
                    last_write = i
                if first_await == len(body) and any(
                        isinstance(x, ast.Await) for x in ast.walk(stmt)):
                    first_await = i
            if last_write > first_await:
                suspect += 1
                offenders.append(f"{path.relative_to(root)}")

    return {"handlers": total, "write_after_await": suspect, "offenders": offenders}


# ── experiment 3: does one misbehaving module take the system down? ──────────

async def exp_isolation(flight_path, perf, ticks):
    gen = load_flight(flight_path, perf, airports=[])
    bus, sink = build(flight_path, perf)

    async def raises(state):
        raise RuntimeError("deliberate module fault")

    slow_calls = {"n": 0}

    async def slow(state):
        # A module that yields for a whole tick budget, to see whether the bus
        # blocks behind it.
        slow_calls["n"] += 1
        await asyncio.sleep(0.05)

    bus.subscribe(raises)
    bus.subscribe(slow)

    for _ in range(int(600 * gen.flight.base_rate)):
        gen.step()

    per_tick = await replay(gen, bus, sink, ticks)
    return {
        "ticks_completed": len(per_tick),
        "ticks_requested": ticks,
        "alerts_still_produced": len(sink.events),
        "slow_module_invocations": slow_calls["n"],
        "latency_with_faulty_modules": stats(per_tick),
    }


# ── experiment 4: memory over a long replay ─────────────────────────────────

async def exp_memory(flight_path, perf, ticks):
    tracemalloc.start()
    gen = load_flight(flight_path, perf, airports=[])
    bus, sink = build(flight_path, perf)
    base = tracemalloc.take_snapshot()
    after_n = []
    for i in range(ticks):
        if gen.finished:
            break
        await bus.publish(gen.step())
        if i and i % (ticks // 4) == 0:
            cur, peak = tracemalloc.get_traced_memory()
            after_n.append({"tick": i, "current_kb": round(cur / 1024, 1)})
    cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    del base
    return {"samples": after_n, "final_kb": round(cur / 1024, 1),
            "peak_kb": round(peak / 1024, 1)}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--flight", required=True)
    ap.add_argument("--ticks", type=int, default=4000)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--out", default=os.path.join(HERE, "bench_results.json"))
    args = ap.parse_args()

    perf = get_performance("DASHLINK", airport_elev_ft=0.0)
    probe = load_flight(args.flight, perf, airports=[])
    rate = probe.flight.base_rate

    full, _ = build(args.flight, perf)
    total_modules = len(full._subscribers)
    points = [p for p in (1, 5, 10, 20, 30, 40, total_modules) if p <= total_modules]

    print(f"flight     : {os.path.basename(args.flight)}")
    print(f"rate       : {rate:g} Hz   budget {1000.0 / rate:.2f} ms/tick")
    print(f"modules    : {total_modules}")
    print(f"ticks/point: {args.ticks}\n")

    print("1. per-tick cost vs module count")
    scaling = await exp_scaling(args.flight, perf, args.ticks, points)

    print("\n2. determinism across repeated replays")
    det = await exp_determinism(args.flight, perf, args.ticks, args.runs)
    print(f"   {det['runs']} runs, {det['events']} alert events each, "
          f"identical: {det['identical']}")
    audit = exp_await_audit()
    print(f"   static audit: {audit['handlers']} handlers, "
          f"{audit['write_after_await']} write to state after awaiting")

    print("\n3. isolation under a raising and a blocking module")
    iso = await exp_isolation(args.flight, perf, min(args.ticks, 300))
    print(f"   completed {iso['ticks_completed']}/{iso['ticks_requested']} ticks, "
          f"{iso['alerts_still_produced']} alerts still produced")
    print(f"   p50 {iso['latency_with_faulty_modules']['p50_ms']:.2f} ms "
          f"(blocking module holds the tick)")

    print("\n4. memory over a long replay")
    mem = await exp_memory(args.flight, perf, args.ticks)
    print(f"   final {mem['final_kb']:.1f} KB, peak {mem['peak_kb']:.1f} KB")

    results = {
        "flight": os.path.basename(args.flight),
        "sample_rate_hz": rate,
        "tick_budget_ms": round(1000.0 / rate, 3),
        "modules": total_modules,
        "ticks_per_point": args.ticks,
        "python": sys.version.split()[0],
        "scaling": scaling,
        "determinism": det,
        "await_audit": audit,
        "isolation": iso,
        "memory": mem,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"\nwritten -> {args.out}")


asyncio.run(main())
