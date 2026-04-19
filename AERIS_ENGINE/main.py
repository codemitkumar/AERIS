"""
AERIS Engine — main entry point.

Usage:
    python main.py [model]

  model  One of: c172p  A320  737  A330-223  787-8  B747  C130
         Default: c172p

The simulation starts from brake release on VIDP Runway 10, climbs to
cruise altitude, then stops.  All flight data is streamed via WebSocket
on ws://localhost:8765 at 60 Hz.
"""

import asyncio
import os
import subprocess
import sys
import time

from data.ingestion.jsbsim_client      import JSBSimClient, GROUND_START_IC
from data.ingestion.aircraft_performance import get_performance, list_models
from communication.websocket_server     import WebSocketServer
from core.takeoff_manager               import TakeoffManager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# ── FlightGear launcher ───────────────────────────────────────────────────────
def launch_flightgear():
    bat_path = os.path.join(BASE_DIR, "start_flightgear.bat")
    subprocess.Popen(["cmd", "/c", bat_path], creationflags=subprocess.CREATE_NEW_CONSOLE)
    print("FlightGear launching... waiting 120 seconds.")
    time.sleep(120)


# ── Main async entry ──────────────────────────────────────────────────────────
async def async_main(model: str):
    # 1. Load performance data + compute V-speeds
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

    # 2. Build JSBSim client (ground start)
    client = JSBSimClient(
        model=model,
        ic=GROUND_START_IC,
        fuel_per_tank_lbs=None,          # use half-capacity default
        default_controls={
            "throttle": 0.0,             # TakeoffManager sets TOGA at brake release
            "rudder":   0.0,   # TakeoffManager controls rudder via elevator law; no static bias
            "gear":     1.0,             # gear down at start
        },
    )

    # 3. Start WebSocket server + takeoff manager concurrently
    ws      = WebSocketServer(host="0.0.0.0", port=8765)
    manager = TakeoffManager(client, perf, ws=ws, dt=1.0/60.0)

    # Wire inbound WS commands to the failure injection API
    async def _handle_command(msg: dict):
        cmd = msg.get("cmd", "")
        if cmd == "inject_failure":
            jsb_s = manager.client.get_state().get("jsbsim", {})
            # manager.inject_failure(
            #     side              = msg.get("side", "captain"),
            #     failure_type_str  = msg.get("type", "none"),
            #     severity          = float(msg.get("severity", 1.0)),
            #     sim_time          = float(msg.get("sim_time", 0.0)),
            #     current_ias       = jsb_s.get("velocities/vc-kts") or 0.0,
            #     current_tas       = jsb_s.get("velocities/vtrue-kts") or 0.0,
            #     current_alt       = jsb_s.get("position/h-sl-ft") or 0.0,
            #     current_mach      = jsb_s.get("velocities/mach") or 0.0,
            # )
        elif cmd == "clear_failure":
            manager.clear_failure(msg.get("side", "captain"))
        elif cmd == "status":
            print(f"[CMD] status — phase={manager.phase.name}")
        else:
            print(f"[CMD] unknown command: {cmd}")

    ws.register_command_handler(_handle_command)

    await asyncio.gather(
        ws.start(),
        manager.run(),
    )


def main():
    model = sys.argv[1] if len(sys.argv) > 1 else "c172p"
    # launch_flightgear()
    asyncio.run(async_main(model))


if __name__ == "__main__":
    main()
