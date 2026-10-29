"""Replay an NTSB docket through the full AERIS stack and record what it did.

Every module, the Layer 2 assessment and GRACE run exactly as they do live
(modules.registry.register_all); the only difference is that alerts are
captured with the FDR time they fired at instead of going to a WebSocket.

    python -m tests.ntsb_harness DCA22LA178          # alert timeline
    python -m tests.ntsb_harness DCA22LA178 --grace  # plus GRACE picks

Case fixtures live in tests/ntsb_cases/<ID>.json: "docket" is the metadata
the loader needs (route, QNH, aircraft), "expect" is what the test asserts.
"""

import argparse
import asyncio
import contextlib
import io
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from core.alert_tracker import AlertTracker            # noqa: E402
from core.data_bus import DataBus                      # noqa: E402
from data.ingestion.faa_airport_loader import load_airports  # noqa: E402
from data.ingestion.ntsb_fdr_loader import load_case, ntsb_dir, find_csv  # noqa: E402
from modules.registry import register_all              # noqa: E402

CASES_DIR = Path(__file__).resolve().parent / "ntsb_cases"


def list_cases() -> list[str]:
    return sorted(p.stem for p in CASES_DIR.glob("*.json"))


def load_fixture(case_id: str) -> dict:
    with open(CASES_DIR / f"{case_id}.json", encoding="utf-8") as f:
        return json.load(f)


def data_available(case_id: str) -> bool:
    d = ntsb_dir() / case_id
    return d.is_dir() and find_csv(d) is not None


def to_seconds(clock: str) -> float:
    h, m, s = clock.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


@dataclass
class AlertEvent:
    t: float            # FDR time of day, seconds
    clock: str
    topic: str          # alert | alert_clear
    id: str
    severity: str = ""
    msg: str = ""
    phase: str = ""


@dataclass
class ReplayResult:
    case_id: str
    events: list[AlertEvent] = field(default_factory=list)
    # (fdr_t, clock, phase, recommendation) whenever GRACE produced one
    recommendations: list[tuple] = field(default_factory=list)
    phases: list[tuple] = field(default_factory=list)
    ticks: int = 0

    def raised(self, alert_id=None, start=None, end=None, severity=None):
        out = []
        for e in self.events:
            if e.topic != "alert":
                continue
            if alert_id and e.id != alert_id:
                continue
            if start is not None and e.t < start:
                continue
            if end is not None and e.t > end:
                continue
            if severity and e.severity != severity:
                continue
            out.append(e)
        return out

    def active_during(self, alert_id, start, end) -> bool:
        """True if alert_id was raised in, or already active going into, [start, end]."""
        active = False
        for e in self.events:
            if e.id != alert_id:
                continue
            if e.t > end:
                break
            if e.t < start:
                active = e.topic == "alert"
            elif e.topic == "alert":
                return True
        return active

    def recommendation_at(self, t: float):
        best = None
        for rec in self.recommendations:
            if rec[0] <= t:
                best = rec
            else:
                break
        return best


class _Recorder:
    """Stands in for the WebSocket server under AlertTracker."""

    def __init__(self):
        self.events: list[AlertEvent] = []
        self.state: dict = {}

    async def broadcast_alert(self, payload: dict) -> None:
        s = self.state
        self.events.append(AlertEvent(
            t=s.get("fdr_time_s", 0.0), clock=s.get("fdr_clock", ""),
            topic=payload.get("topic", ""), id=payload.get("id", ""),
            severity=payload.get("severity", ""), msg=payload.get("msg", ""),
            phase=s.get("phase", ""),
        ))

    async def broadcast(self, data: dict) -> None:
        pass


async def _replay(case_id: str, rate_hz: float, quiet: bool) -> ReplayResult:
    fixture = load_fixture(case_id)
    airports = load_airports()
    gen = load_case(case_id, airports=airports, rate_hz=rate_hz,
                    meta=fixture.get("docket", {}))

    rec = _Recorder()
    tracker = AlertTracker(rec)
    bus = DataBus()
    register_all(bus, ws=tracker, perf=gen.perf)

    result = ReplayResult(case_id)
    result.phases = [(gen.flight.clock(t), p.name) for t, p in gen.flight.phases]
    sink = io.StringIO() if quiet else None
    with contextlib.redirect_stdout(sink) if quiet else contextlib.nullcontext():
        while not gen.finished:
            state = gen.step()
            rec.state = state
            await bus.publish(state)
            result.ticks += 1
            reco = state.get("diversion_recommendation")
            if reco:
                result.recommendations.append(
                    (state["fdr_time_s"], state["fdr_clock"], state["phase"], reco))
    result.events = rec.events
    return result


def run_case(case_id: str, rate_hz: float = 4.0, quiet: bool = True) -> ReplayResult:
    return asyncio.run(_replay(case_id, rate_hz, quiet))


def _print_report(res: ReplayResult, grace: bool) -> None:
    print(f"== {res.case_id}: {res.ticks} ticks")
    print("phases: " + "  ".join(f"{c} {p}" for c, p in res.phases))
    for e in res.events:
        mark = "+" if e.topic == "alert" else "-"
        print(f"  {e.clock} {mark} {e.id:<16} {e.severity:<8} {e.msg:<32} [{e.phase}]")
    if grace:
        last = None
        for t, clock, phase, reco in res.recommendations:
            top = [c["icao"] for c in reco["candidates"]]
            key = (reco["severity"], tuple(top))
            if key != last:
                print(f"  {clock} GRACE {reco['severity']:<8} {top}  "
                      f"conds={reco['activeConditions']}  [{phase}]")
                last = key


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("case", nargs="?", help="docket id (default: all with data)")
    ap.add_argument("--rate", type=float, default=4.0, help="replay tick rate, Hz")
    ap.add_argument("--grace", action="store_true", help="print GRACE picks")
    ap.add_argument("--verbose", action="store_true", help="show module output")
    args = ap.parse_args()
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    cases = [args.case] if args.case else [c for c in list_cases() if data_available(c)]
    for c in cases:
        _print_report(run_case(c, rate_hz=args.rate, quiet=not args.verbose), args.grace)


if __name__ == "__main__":
    main()
