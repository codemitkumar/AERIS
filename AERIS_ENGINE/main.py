"""
AERIS Engine — main entry point.

Usage:
    python main.py [model]

  model  One of: c172p  A320  737  A330-223  787-8  B747  C130
         Default: c172p

Flight data is generated via FlightGenerator and streamed via WebSocket
on ws://localhost:8765 at ~30 Hz.
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
    while gen.phase != Phase.COMPLETE:
        state  = gen.step()
        cross  = _cross_check(state)
        record = {**state, **cross}

        if gen.should_log():
            log_fp.write(json.dumps(record) + "\n")
            log_fp.flush()

        if ws:
            await ws.broadcast(record)

        await bus.publish(record)

        await asyncio.sleep(gen.dt)

    print("[AERIS] Flight complete.")


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

    gen = FlightGenerator(perf, AIRPORT_CSV, dt=1/30)
    ws  = WebSocketServer(host="0.0.0.0", port=8765)
    bus = DataBus()
    register_all(bus)

    async def _handle_command(msg: dict):
        cmd = msg.get("cmd", "")
        if cmd == "status":
            print(f"[CMD] status — phase={gen.phase.name}")
        else:
            print(f"[CMD] unknown command: {cmd}")

    ws.register_command_handler(_handle_command)

    import time as _time
    log_path = os.path.join(BASE_DIR, f"flightData_{model}_{int(_time.time())}.jsonl")
    print(f"[AERIS] Logging to {log_path}")
    with open(log_path, "w", encoding="utf-8") as log_fp:
        await asyncio.gather(
            ws.start(),
            flight_loop(gen, ws, log_fp, bus),
        )


def main():
    model = sys.argv[1] if len(sys.argv) > 1 else "c172p"
    asyncio.run(async_main(model))


if __name__ == "__main__":
    main()
