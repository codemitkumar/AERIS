"""
AERIS Engine — main entry point.

Usage:
    python main.py [model]

  model  One of: c172p  A320  737  A330-223  787-8  B747  C130
         Default: c172p

Flight data is generated via FlightGenerator and streamed via WebSocket
on ws://localhost:8765 at ~30 Hz.

Terminal injection commands (type while simulation is running):
    inject [captain|fo|both] [rate]   — start unreliable IAS fault
    clear                             — stop fault injection
    status                            — print current sim state
"""

import asyncio
import json
import os
import sys

from data.ingestion.FlightGenerator      import FlightGenerator, Phase
from data.ingestion.aircraft_performance import get_performance, list_models
from communication.websocket_server      import WebSocketServer
from core.data_bus                       import DataBus
from modules.registry                    import register_all
from emergencyInjector.unreliable_airspeed import UnreliableAirspeedInjector

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
AIRPORT_CSV = os.path.join(BASE_DIR, "airports.csv")

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


async def flight_loop(gen: FlightGenerator, ws: WebSocketServer, log_fp, bus: DataBus):
    v1  = gen.perf.v1_kts
    vr  = gen.perf.vr_kts
    v2  = gen.perf.v2_kts

    _v1_logged = False
    _vr_logged = False
    _v2_logged = False

    while gen.phase != Phase.COMPLETE:
        state  = gen.step()
        cross  = _cross_check(state)
        record = {**state, **cross}

        gs = state["groundspeed_kts"]

        # V-speed milestone logging
        if not _v1_logged and gs >= v1:
            _v1_logged = True
            print(f"[FLIGHT] *** V1 ({v1:.1f} kts) reached — t={gen.time:.1f}s — GO/NO-GO decision point ***")
        if not _vr_logged and gs >= vr:
            _vr_logged = True
            print(f"[FLIGHT] *** VR ({vr:.1f} kts) reached — t={gen.time:.1f}s — ROTATE ***")
        if not _v2_logged and gs >= v2:
            _v2_logged = True
            print(f"[FLIGHT] *** V2 ({v2:.1f} kts) reached — t={gen.time:.1f}s — climb speed ***")

        if gen.should_log():
            log_fp.write(json.dumps(record) + "\n")
            log_fp.flush()

        if ws:
            await ws.broadcast(record)

        await bus.publish(record)

        await asyncio.sleep(gen.dt)

    print("[AERIS] Flight complete.")


async def terminal_command_reader(injector: UnreliableAirspeedInjector, gen: FlightGenerator):
    """Read injection commands from stdin while simulation runs."""
    loop = asyncio.get_event_loop()
    print("[CMD] Terminal ready — commands: inject [captain|fo|both] [rate]  |  clear  |  status")
    while True:
        try:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                break
            parts = line.strip().split()
            if not parts:
                continue
            cmd = parts[0].lower()
            if cmd == "inject":
                side = parts[1] if len(parts) > 1 else "captain"
                rate = float(parts[2]) if len(parts) > 2 else -0.1
                injector.start(side=side, rate=rate)
            elif cmd == "clear":
                injector.stop()
            elif cmd == "status":
                print(
                    f"[STATUS] phase={gen.phase.name}  "
                    f"drift={injector.drift_pct:+.1f}%  active={injector.active}"
                )
            else:
                print(f"[CMD] Unknown: '{cmd}'  —  inject [captain|fo|both] [rate]  |  clear  |  status")
        except (EOFError, KeyboardInterrupt):
            break
        except Exception as exc:
            print(f"[CMD] Error: {exc}")


def _build_final_output(gen: FlightGenerator, jsonl_path: str, sim_dir: str) -> str:
    """Read the temp JSONL, wrap in the final schema, write JSON, delete JSONL."""
    import time as _time

    simulation_data = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                simulation_data.append(json.loads(line))

    dist_nm = gen.route_distance_nm
    flight_hrs = dist_nm / gen._cruise_speed if gen._cruise_speed > 0 else 0
    hours   = int(flight_hrs)
    minutes = int((flight_hrs - hours) * 60)

    def apt_code(apt):
        return apt.get("iata") or apt.get("icao") or "UNKN"

    output = {
        "aircraftName":  gen.perf.name,
        "originAirport": apt_code(gen.origin),
        "destination":   apt_code(gen.dest),
        "distance":      f"{dist_nm:.0f} nm",
        "expectedTime":  f"{hours}h {minutes}m",
        "simulationData": simulation_data,
    }

    safe_model = gen.perf.model.replace("-", "_")
    final_path = os.path.join(sim_dir, f"flight_{safe_model}_{int(_time.time())}.json")
    with open(final_path, "w", encoding="utf-8") as f:
        json.dump(output, f)

    os.remove(jsonl_path)
    return final_path


async def async_main(model: str):
    try:
        perf = get_performance(model, airport_elev_ft=0.0)
    except KeyError:
        print(f"[ERROR] Unknown model '{model}'.  Available: {list_models()}")
        return

    print(
        f"[AERIS] Aircraft : {perf.name}\n"
        f"[AERIS] V1={perf.v1_kts:.1f}  VR={perf.vr_kts:.1f}  "
        f"V2={perf.v2_kts:.1f} kts  →  cruise FL{int(perf.cruise_alt_ft/100):03d}"
    )

    injector = UnreliableAirspeedInjector()
    gen = FlightGenerator(perf, AIRPORT_CSV, dt=1/30, emergency_fn=injector)
    ws  = WebSocketServer(host="0.0.0.0", port=8765)
    bus = DataBus()
    register_all(bus, ws=ws)

    import time as _time
    sim_dir  = os.path.join(BASE_DIR, "simulationdata")
    os.makedirs(sim_dir, exist_ok=True)
    tmp_path = os.path.join(sim_dir, f"_tmp_{model}_{int(_time.time())}.jsonl")

    origin_code = gen.origin.get("iata") or gen.origin.get("icao") or "?"
    dest_code   = gen.dest.get("iata")   or gen.dest.get("icao")   or "?"
    print(
        f"[AERIS] Route    : {origin_code} → {dest_code}  "
        f"({gen.route_distance_nm:.0f} nm)  "
        f"cruise interval {gen._log_interval:.0f} s"
    )

    ws_task  = asyncio.create_task(ws.start())
    cmd_task = asyncio.create_task(terminal_command_reader(injector, gen))

    with open(tmp_path, "w", encoding="utf-8") as log_fp:
        await flight_loop(gen, ws, log_fp, bus)

    ws_task.cancel()
    cmd_task.cancel()

    final = _build_final_output(gen, tmp_path, sim_dir)
    print(f"[AERIS] Saved → {final}")


def main():
    model = sys.argv[1] if len(sys.argv) > 1 else "c172p"
    asyncio.run(async_main(model))


if __name__ == "__main__":
    main()
