"""
AERIS Engine — main entry point.

Usage:
    python main.py [model] [--source xplane|ntsb] [--flight ID_OR_PATH]

  model   One of: c172p  A320  737  A330-223  787-8  B747  C130
          Default: the aircraft loaded in X-Plane / the docket's own type

  --source xplane      live feed from a running X-Plane 11/12 (UDP 49000),
                       the default; --flight is the destination ICAO.
                       Emergencies come from X-Plane's failure menu.
  --source ntsb        replay an NTSB docket FDR attachment, e.g.
                       --flight DCA22LA178 (see scripts/fetch_ntsb_docket.py)

Flight data streams over WebSocket on ws://localhost:8765. The control panel
at /controlPanel drives the same commands the terminal does — see
communication/commands.py for the shared dispatcher.
"""

import argparse
import asyncio
import json
import os
import sys

if sys.stdout.encoding is None or sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from data.ingestion.aircraft_performance  import get_performance, list_models
from communication.websocket_server       import WebSocketServer
from communication.commands               import (CommandDispatcher, parse_terminal,
                                                  terminal_help)
from core.data_bus                        import DataBus
from modules.registry                     import register_all
from core.alert_tracker                   import AlertTracker
from emergencyInjector.injection_manager  import InjectionManager
from config.settings                      import check_data_root

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

_IAS_DISAGREE_KTS   = 5.0
_ALT_DISAGREE_FT    = 200.0
_PITCH_DISAGREE_DEG = 2.0
_ROLL_DISAGREE_DEG  = 3.0


def _cross_check(state: dict) -> dict:
    ias_delta   = abs(state["ias_captain"]   - state["ias_fo"])
    alt_delta   = abs(state["alt_captain"]   - state["alt_fo"])
    pitch_delta = abs(state["pitch_captain"] - state["pitch_fo"])
    roll_delta  = abs(state["roll_captain"]  - state["roll_fo"])
    gs          = state["groundspeed_kts"]

    any_dis = (
        ias_delta   > _IAS_DISAGREE_KTS  or
        alt_delta   > _ALT_DISAGREE_FT   or
        pitch_delta > _PITCH_DISAGREE_DEG or
        roll_delta  > _ROLL_DISAGREE_DEG
    )

    def _suspect(my_ias, other_ias):
        if ias_delta > _IAS_DISAGREE_KTS:
            if abs(my_ias - gs) > abs(other_ias - gs) + 10:
                return True
            if my_ias < 60 and other_ias > 100:
                return True
            if my_ias > 500:
                return True
        return False

    cap_suspect  = _suspect(state["ias_captain"], state["ias_fo"])
    fo_suspect   = _suspect(state["ias_fo"],      state["ias_captain"])
    avg_ias      = (state["ias_captain"] + state["ias_fo"]) / 2
    both_suspect = (ias_delta > _IAS_DISAGREE_KTS and cap_suspect and fo_suspect) or \
                   (avg_ias < 60 and gs > 100)

    if both_suspect:
        action = "UNRELIABLE_AIRSPEED_BOTH"
    elif cap_suspect:
        action = "CHECK_CAPTAIN"
    elif fo_suspect:
        action = "CHECK_FO"
    else:
        action = "NORMAL"

    return {
        "any_disagree":       any_dis,
        "ias_disagree":       ias_delta   > _IAS_DISAGREE_KTS,
        "alt_disagree":       alt_delta   > _ALT_DISAGREE_FT,
        "pitch_disagree":     pitch_delta > _PITCH_DISAGREE_DEG,
        "roll_disagree":      roll_delta  > _ROLL_DISAGREE_DEG,
        "captain_suspect":    cap_suspect,
        "fo_suspect":         fo_suspect,
        "both_suspect":       both_suspect,
        "recommended_action": action,
    }


class FlightSession:
    """Owns whichever flight is currently running.

    The control panel can swap the source out from under a running flight, so
    the generator, the bus and the modules are all rebuilt together — module
    instances carry rolling windows and alert latches, and carrying those
    across a source change would leak one flight's history into the next.
    """

    def __init__(self, ws: WebSocketServer, manager: InjectionManager):
        self.ws = ws
        self.manager = manager
        self.gen = None
        self.bus = None
        self.task = None
        self.log_path = None
        self._resume = asyncio.Event()
        self._resume.set()
        # Set only when a flight runs to its end. Loading another flight
        # cancels the current task, so the caller can't just await that task —
        # it would see the cancellation and think the engine was finished.
        self.ended = asyncio.Event()

    @property
    def paused(self) -> bool:
        return not self._resume.is_set()

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    # ── building ─────────────────────────────────────────────────────────────

    def build(self, source: str, model: str | None = None, flight: str | None = None):
        import time as _time

        if source == "ntsb":
            from data.ingestion.ntsb_fdr_loader import load_case, list_cases, ntsb_dir
            from data.ingestion.faa_airport_loader import load_airports

            cases = list_cases()
            case = flight or (cases[0] if cases else None)
            if case is None:
                raise FileNotFoundError(
                    f"No NTSB dockets with FDR data under {ntsb_dir()}. "
                    f"Fetch one with scripts/fetch_ntsb_docket.py <ProjectID>.")
            gen = load_case(case, airports=load_airports(), emergency_fn=self.manager)
            perf = gen.perf
            gen.source_label = f"ntsb:{gen.meta.get('ntsb_id') or case}"
            print(f"[AERIS] Replaying NTSB {gen.source_label[5:]} ({perf.name})  "
                  f"{gen.flight.duration_s / 60:.1f} min @ {1 / gen.dt:g} Hz")
        elif source == "xplane":
            from data.ingestion.xplane_loader import XPlaneLive

            gen = XPlaneLive(perf=get_performance(model, airport_elev_ft=0.0) if model else None,
                             dest_icao=flight, emergency_fn=self.manager)
            perf = gen.perf
            gen.source_label = f"xplane:{gen.acf_icao or perf.model}"
            print(f"[AERIS] Live from X-Plane: {gen.acf_icao or '?'} as {perf.name} "
                  f"@ {1 / gen.dt:g} Hz")
        else:
            raise ValueError(f"Unknown source '{source}' — use xplane or ntsb")

        tracker = AlertTracker(self.ws)
        bus = DataBus()
        register_all(bus, ws=tracker, perf=perf)

        sim_dir = os.path.join(BASE_DIR, "simulationdata")
        os.makedirs(sim_dir, exist_ok=True)
        tag = source

        self.gen = gen
        self.bus = bus
        self.perf = perf
        self.source = source
        self.sim_dir = sim_dir
        self.log_path = os.path.join(sim_dir, f"_tmp_{tag}_{int(_time.time())}.jsonl")

        origin = gen.origin.get("iata") or gen.origin.get("icao") or "?"
        dest = gen.dest.get("iata") or gen.dest.get("icao") or "?"
        print(f"[AERIS] Route    : {origin} → {dest}  ({gen.route_distance_nm:.0f} nm)")
        return gen

    # ── running ──────────────────────────────────────────────────────────────

    async def start(self):
        await self.stop()
        self._resume.set()
        self.ended.clear()
        await self.ws.broadcast_meta({
            "topic":             "flight_meta",
            "model":             self.perf.name,
            "source":            self.gen.source_label,
            "origin_icao":       self.gen.origin.get("icao", ""),
            "destination_icao":  self.gen.dest.get("icao", ""),
            "route_distance_nm": round(self.gen.route_distance_nm, 1),
            "departure_heading": round(self.gen.departure_heading, 1),
            "landing_heading":   round(self.gen.landing_heading, 1),
            "sample_rate_hz":    round(1.0 / self.gen.dt, 3),
        })
        self.task = asyncio.create_task(self._run())

    async def stop(self):
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        self.task = None

    def pause(self):
        self._resume.clear()

    def resume(self):
        self._resume.set()

    async def _run(self):
        gen, bus = self.gen, self.bus
        loop = asyncio.get_event_loop()

        try:
            await self._fly(gen, bus, loop)
        except asyncio.CancelledError:
            # Superseded by another flight — the partial log is of no use.
            if self.log_path and os.path.exists(self.log_path):
                os.remove(self.log_path)
            raise

        print("[AERIS] Flight complete.")
        await self._finalise()
        self.ended.set()

    async def _fly(self, gen, bus, loop):
        with open(self.log_path, "w", encoding="utf-8") as log_fp:
            while not gen.finished:
                if self.paused:
                    await self._resume.wait()

                tick_start = loop.time()
                state = gen.step()
                # Modules run before `record` is built so their added keys
                # (assessment, diversion_recommendation) reach the feed too.
                await bus.publish(state)
                record = {**state, **_cross_check(state)}

                if gen.should_log():
                    log_fp.write(json.dumps(record) + "\n")
                    log_fp.flush()

                await self.ws.broadcast(record)

                # Sleep only the remainder of the tick budget, so a 2-hour
                # flight takes two hours of wall clock.
                remaining = gen.dt - (loop.time() - tick_start)
                if remaining > 0:
                    await asyncio.sleep(remaining)

    async def _finalise(self):
        final = _build_final_output(
            self.gen, self.log_path, self.sim_dir,
            self.manager.injected_summary, self.manager.notam.closures,
        )
        print(f"[AERIS] Saved → {final}")
        if self.manager.injected_summary:
            print(f"[AERIS] Injected emergencies recorded: {self.manager.injected_summary}")
        await self.ws.broadcast({"topic": "flight_complete", "file": os.path.basename(final)})


def _build_final_output(gen, jsonl_path, sim_dir,
                        injected_emergency=None, notam_closures=None) -> str:
    """Read the temp JSONL, wrap in the final schema, write JSON, delete JSONL."""
    import time as _time

    simulation_data = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                simulation_data.append(json.loads(line))

    dist_nm    = gen.route_distance_nm
    flight_hrs = dist_nm / gen._cruise_speed if gen._cruise_speed > 0 else 0
    hours      = int(flight_hrs)
    minutes    = int((flight_hrs - hours) * 60)

    def apt_code(apt):
        return apt.get("iata") or apt.get("icao") or "UNKN"

    output = {
        "aircraftName":  gen.perf.name,
        "originAirport": apt_code(gen.origin),
        "destination":   apt_code(gen.dest),
        "distance":      f"{dist_nm:.0f} nm",
        "expectedTime":  f"{hours}h {minutes}m",
        "source":        getattr(gen, "source_label", "unknown"),
    }

    if injected_emergency:
        output["injectedEmergency"] = injected_emergency

    if notam_closures:
        output["notamClosures"] = [
            {
                "icao":    c["icao"],
                "onPath":  c["on_path"],
                "summary": c["notam"].summary,
                "raw":     c["notam"].raw,
            }
            for c in notam_closures
        ]

    output["simulationData"] = simulation_data

    safe_model = gen.perf.model.replace("-", "_")
    final_path = os.path.join(sim_dir, f"flight_{safe_model}_{int(_time.time())}.json")
    with open(final_path, "w", encoding="utf-8") as f:
        json.dump(output, f)

    os.remove(jsonl_path)
    return final_path


async def terminal_command_reader(dispatcher: CommandDispatcher):
    """Read injection commands from stdin while the simulation runs."""
    loop = asyncio.get_event_loop()
    print(terminal_help())

    while True:
        try:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                break
            msg = parse_terminal(line)
            if msg:
                await dispatcher.handle(msg)
        except (EOFError, KeyboardInterrupt):
            break
        except Exception as exc:
            print(f"[CMD] Error: {exc}")


async def async_main(args):
    check_data_root()

    ws = WebSocketServer(host="0.0.0.0", port=8765)
    manager = InjectionManager()
    session = FlightSession(ws, manager)
    dispatcher = CommandDispatcher(manager, ws)
    dispatcher.attach_session(session, default_model=args.model)
    ws.register_command_handler(dispatcher.handle)

    try:
        session.build(args.source, model=args.model, flight=args.flight)
    except (KeyError, FileNotFoundError, ConnectionError) as exc:
        if isinstance(exc, KeyError):
            print(f"[ERROR] Unknown model '{args.model}'.  Available: {list_models()}")
        else:
            print(f"[ERROR] {exc}")
        return

    dispatcher.bind(session.gen)

    ws_task = asyncio.create_task(ws.start())
    cmd_task = asyncio.create_task(terminal_command_reader(dispatcher))

    await session.start()
    await session.ended.wait()

    await session.stop()
    ws_task.cancel()
    cmd_task.cancel()


def main():
    ap = argparse.ArgumentParser(description="AERIS engine")
    ap.add_argument("model", nargs="?", default=None,
                    help=f"aircraft profile ({', '.join(list_models())}); "
                         f"default: the aircraft loaded in X-Plane")
    ap.add_argument("--source", choices=("xplane", "ntsb"), default="xplane",
                    help="fly live in X-Plane, or replay an NTSB docket")
    ap.add_argument("--flight", default=None,
                    help="xplane: destination ICAO; ntsb: docket id or path")
    args = ap.parse_args()

    try:
        asyncio.run(async_main(args))
    except KeyboardInterrupt:
        print("\n[AERIS] Stopped.")


if __name__ == "__main__":
    main()
