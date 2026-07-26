"""
AERIS Batch Simulation Generator — AI training data  (parallel edition).

Each simulation is independent, so they run in parallel across all CPU cores
using ProcessPoolExecutor.  Python's GIL is bypassed — each worker is a
separate OS process with its own interpreter and random seed.

GPU note (RTX 5060)
-------------------
The flight simulation itself is a sequential per-tick state machine; no
individual simulation can be parallelised on a GPU.  The GPU becomes useful
at the next stage: training / running inference against the generated data.
The CPU-level parallelism here maximises throughput for dataset generation.

Typical speedup on an 8-core machine: ~7-8x vs sequential.

Output layout
-------------
simulationdata/
  A320/
    flight_A320_<batch_ts>_0001.json
    flight_A320_<batch_ts>_0002.json
    ...
    manifest.json        ← cumulative index, updated each run

Re-running appends to the same model folder — sim numbers continue from the
highest existing index, and the manifest grows in place.

Usage
-----
    python batch_sim.py [model] [count] [workers]

    model    c172p | A320 | 737 | A330-223 | 787-8 | B747 | C130   (default: A320)
    count    Simulations to generate this run                        (default: 100)
    workers  Parallel worker processes (default: all CPU cores)

Examples
--------
    python batch_sim.py A320 100        
    python batch_sim.py A320 100 4      
    python batch_sim.py 737 500 
"""

import json
import os
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

AUTO_INJECT_PROBABILITY = 0.1   # 10 % chance per simulation


# ── worker (must be top-level for multiprocessing pickling) ───────────────────

def _run_worker(args: tuple) -> tuple:
    """Executed in a separate process — imports are fresh per worker.

    args = (perf, sim_index, batch_ts, model)
    Returns (sim_index, output_dict, injected_summary, elapsed_s)
    """
    import asyncio
    from data.ingestion.FlightGenerator      import FlightGenerator, Phase
    from emergencyInjector.injection_manager import InjectionManager
    from core.data_bus                       import DataBus
    from core.alert_tracker                  import AlertTracker
    from modules.registry                    import register_all

    perf, sim_index, batch_ts, model = args
    t0 = time.perf_counter()

    manager = InjectionManager()
    manager.configure_auto_inject(probability=AUTO_INJECT_PROBABILITY)

    async def _simulate():
        bus     = DataBus()
        tracker = AlertTracker()        # no ws — batch mode, no broadcast
        register_all(bus, ws=tracker, perf=perf)

        gen = FlightGenerator(perf, dt=1 / 30, emergency_fn=manager)
        records = []

        while gen.phase != Phase.COMPLETE:
            state = gen.step()
            await bus.publish(state)    # runs all 53 modules + health assessment
            if gen.should_log():
                records.append(dict(state))   # state now contains "assessment"

        return gen, records

    gen, records = asyncio.run(_simulate())

    apt = lambda a: a.get("iata") or a.get("icao") or "UNKN"
    hrs = gen.route_distance_nm / gen._cruise_speed if gen._cruise_speed > 0 else 0
    h, m = int(hrs), int((hrs % 1) * 60)

    output = {
        "aircraftName":    perf.name,
        "originAirport":   apt(gen.origin),
        "destination":     apt(gen.dest),
        "distance":        f"{gen.route_distance_nm:.0f} nm",
        "expectedTime":    f"{h}h {m}m",
        "simulationIndex": sim_index,
    }
    if manager.injected_summary:
        output["injectedEmergency"] = manager.injected_summary
    output["simulationData"] = records

    elapsed = time.perf_counter() - t0
    return sim_index, output, manager.injected_summary, elapsed


# ── manifest helpers ──────────────────────────────────────────────────────────

def _next_sim_index(model_dir: str, model: str) -> int:
    pattern = re.compile(rf"^flight_{re.escape(model)}_\d+_(\d+)\.json$")
    max_idx = 0
    if os.path.isdir(model_dir):
        for fname in os.listdir(model_dir):
            m = pattern.match(fname)
            if m:
                max_idx = max(max_idx, int(m.group(1)))
    return max_idx + 1


def _load_manifest(path: str) -> dict:
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {
        "model": "", "totalSimulations": 0,
        "injectedCount": 0, "cleanCount": 0,
        "injectRatePct": 0.0, "simulations": [],
    }


def _save_manifest(path: str, manifest: dict) -> None:
    total    = manifest["totalSimulations"]
    injected = manifest["injectedCount"]
    manifest["cleanCount"]    = total - injected
    manifest["injectRatePct"] = round(injected / total * 100, 1) if total else 0.0
    # Keep entries sorted by sim index for clean AI dataset loading
    manifest["simulations"].sort(key=lambda e: e["simulationIndex"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)


# ── batch runner ──────────────────────────────────────────────────────────────

def run_batch(model: str, count: int = 100, workers: int | None = None) -> None:
    """Generate `count` simulations in parallel and save to simulationdata/<model>/."""
    from data.ingestion.aircraft_performance import get_performance, list_models

    try:
        perf = get_performance(model, airport_elev_ft=0.0)
    except KeyError:
        print(f"[ERROR] Unknown model '{model}'.  Available: {list_models()}")
        return

    cpu_count = os.cpu_count() or 1
    workers   = min(workers or cpu_count, count, cpu_count)

    model_dir     = os.path.join(BASE_DIR, "simulationdata", model)
    os.makedirs(model_dir, exist_ok=True)
    manifest_path = os.path.join(model_dir, "manifest.json")

    manifest  = _load_manifest(manifest_path)
    manifest["model"] = perf.name

    batch_ts  = int(time.time())
    start_idx = _next_sim_index(model_dir, model)
    end_idx   = start_idx + count - 1

    print(
        f"\n{'='*66}\n"
        f"  AERIS Batch Simulation Generator  [PARALLEL]\n"
        f"{'='*66}\n"
        f"  Aircraft   : {perf.name}\n"
        f"  This run   : {count} simulations  (#{start_idx} – #{end_idx})\n"
        f"  Workers    : {workers} / {cpu_count} CPU cores\n"
        f"  Inject prob: {AUTO_INJECT_PROBABILITY * 100:.0f}%\n"
        f"  Output dir : {model_dir}\n"
        f"{'='*66}"
    )

    # Build args for every simulation upfront
    all_args = [
        (perf, sim_index, batch_ts, model)
        for sim_index in range(start_idx, start_idx + count)
    ]

    completed       = 0
    injected_count  = 0
    wall_start      = time.time()
    new_entries     = []   # collected here, written to manifest at end

    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_run_worker, args): args[1] for args in all_args}

        for future in as_completed(futures):
            try:
                sim_index, output, injected, elapsed = future.result()
            except Exception as exc:
                sim_index = futures[future]
                print(f"  [ERROR] sim #{sim_index:04d} failed: {exc}")
                continue

            # Write sim file immediately — each has a unique name, no race
            fname = f"flight_{model}_{batch_ts}_{sim_index:04d}.json"
            fpath = os.path.join(model_dir, fname)
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(output, f)

            completed += 1
            if injected:
                injected_count += 1
                tag = f"INJECTED  {injected}"
            else:
                tag = "clean"

            # ETA based on wall time so far
            wall_elapsed = time.time() - wall_start
            avg_s    = wall_elapsed / completed
            eta_s    = avg_s * (count - completed)
            eta_str  = f"{int(eta_s // 60)}m {int(eta_s % 60)}s" if eta_s > 0 else "--"

            print(
                f"  [done {completed:4d}/{count}]  "
                f"#{sim_index:04d}  "
                f"{output['originAirport']:4s} → {output['destination']:4s}  "
                f"{output['distance']:8s}  "
                f"{elapsed:4.1f}s  │  {tag}  "
                f"(ETA {eta_str})"
            )

            new_entries.append({
                "file":              fname,
                "simulationIndex":   sim_index,
                "origin":            output["originAirport"],
                "destination":       output["destination"],
                "distance":          output["distance"],
                "injectedEmergency": injected,
            })

    # ── update and save manifest ──────────────────────────────────────────────
    manifest["totalSimulations"] += completed
    manifest["injectedCount"]    += injected_count
    manifest["simulations"].extend(new_entries)
    _save_manifest(manifest_path, manifest)

    total_s         = time.time() - wall_start
    total_in_folder = manifest["totalSimulations"]
    throughput      = completed / total_s if total_s > 0 else 0

    print(
        f"\n{'='*66}\n"
        f"  Parallel run complete\n"
        f"  Wall time  : {total_s:.1f}s  ({total_s / completed:.2f}s avg per sim)\n"
        f"  Throughput : {throughput:.1f} sims/sec  ({workers} workers)\n"
        f"  This run   →  clean: {completed - injected_count}  "
        f"injected: {injected_count}  ({injected_count / completed * 100:.1f}%)\n"
        f"  Folder total: {total_in_folder} simulations\n"
        f"  Manifest   : {manifest_path}\n"
        f"{'='*66}\n"
    )


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # multiprocessing on Windows requires this guard
    import multiprocessing
    multiprocessing.freeze_support()

    _model   = sys.argv[1] if len(sys.argv) > 1 else "A320"
    _count   = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    _workers = int(sys.argv[3]) if len(sys.argv) > 3 else None

    run_batch(_model, _count, _workers)
