"""Throughput, CPU utilisation and memory footprint.

A reviewer asked for CPU/memory overhead and throughput, none of which the
previous version reported. Throughput is measured unpaced (how fast the
pipeline can consume ticks); utilisation is measured paced at the recording's
native rate, which is how it would actually run.
"""

import argparse
import asyncio
import json
import os
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
    async def broadcast_alert(self, payload):
        pass

    async def broadcast(self, data):
        pass

    async def broadcast_meta(self, data):
        pass


def rss_mb():
    """Resident set size in MB, or (None, None) if unavailable."""
    try:
        import psutil
        import os
        info = psutil.Process(os.getpid()).memory_info()
        return info.rss / 1e6, getattr(info, "peak_wset", info.rss) / 1e6
    except Exception:
        return None, None


def cpu_seconds():
    return time.process_time()


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--flight", required=True)
    ap.add_argument("--ticks", type=int, default=20000)
    ap.add_argument("--out", default=os.path.join(HERE, "resource_results.json"))
    args = ap.parse_args()

    perf = get_performance("DASHLINK", airport_elev_ft=0.0)

    base_rss, _ = rss_mb()

    # -- unpaced: how many ticks per second can the pipeline consume? --------
    bus = DataBus()
    register_all(bus, ws=AlertTracker(Sink()), perf=perf)
    gen = load_flight(args.flight, perf, airports=[])
    rate = gen.flight.base_rate
    for _ in range(int(600 * rate)):
        gen.step()

    loaded_rss, _ = rss_mb()

    c0, w0 = cpu_seconds(), time.perf_counter()
    n = 0
    for _ in range(args.ticks):
        if gen.finished:
            break
        await bus.publish(gen.step())
        n += 1
    wall = time.perf_counter() - w0
    cpu = cpu_seconds() - c0

    throughput = n / wall
    end_rss, peak_rss = rss_mb()

    print(f"flight            : {os.path.basename(args.flight)}")
    print(f"native rate       : {rate:g} Hz")
    print(f"\nunpaced throughput: {throughput:,.0f} ticks/s "
          f"({throughput / rate:.0f}x real time)")
    print(f"wall / cpu        : {wall:.2f} s / {cpu:.2f} s")
    print(f"cpu per tick      : {cpu / n * 1e6:.1f} us")
    print(f"utilisation at {rate:g} Hz : "
          f"{cpu / n * rate * 100:.2f}% of one core")

    print(f"\nRSS at start      : {base_rss:.1f} MB")
    print(f"RSS after load    : {loaded_rss:.1f} MB "
          f"(+{loaded_rss - base_rss:.1f} MB for modules + flight)")
    print(f"RSS after {n} ticks: {end_rss:.1f} MB "
          f"(+{end_rss - loaded_rss:.1f} MB during replay)")
    print(f"peak RSS          : {peak_rss:.1f} MB")

    out = {
        "flight": os.path.basename(args.flight),
        "rate_hz": rate,
        "ticks": n,
        "wall_s": round(wall, 3),
        "cpu_s": round(cpu, 3),
        "throughput_ticks_per_s": round(throughput, 1),
        "realtime_factor": round(throughput / rate, 1),
        "cpu_us_per_tick": round(cpu / n * 1e6, 2),
        "utilisation_pct_one_core": round(cpu / n * rate * 100, 3),
        "rss_start_mb": round(base_rss, 1),
        "rss_loaded_mb": round(loaded_rss, 1),
        "rss_end_mb": round(end_rss, 1),
        "rss_peak_mb": round(peak_rss, 1),
        "rss_growth_during_replay_mb": round(end_rss - loaded_rss, 2),
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"\nwritten -> {args.out}")


asyncio.run(main())
