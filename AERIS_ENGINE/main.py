"""
AERIS Engine — main entry point.

Usage:
    python main.py [model]

  model  One of: c172p  A320  737  A330-223  787-8  B747  C130
         Default: c172p

Flight data is generated via FlightGenerator and streamed via WebSocket
on ws://localhost:8765 at ~30 Hz.

Terminal injection commands (type while simulation is running):
    inject ias [captain|fo|both] [rate]          — IAS ADC drift
    inject alt_disagree [captain|fo|both] [rate] — altimeter ADC drift (ft/tick)
    inject descent [rate_fpm]                    — uncommanded altitude loss
    inject windshear [rate_fpm] [duration_s]     — windshear burst (auto-stops)
    inject energy [alt_fpm] [spd_kts_s]          — total energy bleed
    inject turbulence [amplitude_fpm] [freq_hz]  — turbulence / G oscillation
    inject holding [duration_min] [rate_lbs_hr]  — unplanned ATC holding delay
    inject notam [count]                         — airport/runway closure NOTAMs
    clear [ias|alt_disagree|descent|windshear|energy|turbulence|holding|notam]  — clear one
    clear                                        — clear all active injectors
    status                                       — print injector states
"""

import asyncio
import json
import os
import sys

if sys.stdout.encoding is None or sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from data.ingestion.FlightGenerator       import FlightGenerator, Phase
from data.ingestion.aircraft_performance  import get_performance, list_models
from communication.websocket_server       import WebSocketServer
from core.data_bus                        import DataBus
from modules.registry                     import register_all
from core.alert_tracker                   import AlertTracker
from emergencyInjector.injection_manager  import InjectionManager

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


async def flight_loop(gen: FlightGenerator, ws: WebSocketServer, log_fp, bus: DataBus):
    v1  = gen.perf.v1_kts
    vr  = gen.perf.vr_kts
    v2  = gen.perf.v2_kts

    _v1_logged = False
    _vr_logged = False
    _v2_logged = False

    loop = asyncio.get_event_loop()

    while gen.phase != Phase.COMPLETE:
        tick_start = loop.time()

        state = gen.step()
        # Run DataBus modules before building `record`, so their added keys
        # (assessment, diversion_recommendation) reach the WS feed/log too.
        await bus.publish(state)
        cross  = _cross_check(state)
        record = {**state, **cross}

        gs = state["groundspeed_kts"]

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

        # Real-time pacing: sleep only the remainder of the tick budget
        # so a 2-hour flight takes exactly 2 hours of wall-clock time.
        remaining = gen.dt - (loop.time() - tick_start)
        if remaining > 0:
            await asyncio.sleep(remaining)

    print("[AERIS] Flight complete.")


async def terminal_command_reader(manager: InjectionManager, gen: FlightGenerator):
    """Read fault-injection commands from stdin while the simulation runs."""
    loop = asyncio.get_event_loop()
    print(
        "[CMD] Terminal ready — type a command:\n"
        "  inject ias [captain|fo|both] [rate]\n"
        "  inject alt_disagree [captain|fo|both] [rate_ft_per_tick]\n"
        "  inject descent [rate_fpm]\n"
        "  inject windshear [rate_fpm] [duration_s]\n"
        "  inject energy [alt_fpm] [spd_kts_s]\n"
        "  inject turbulence [amplitude_fpm] [freq_hz]\n"
        "  inject fuel_leak [rate_lbs_hr] [left|right|center|all]\n"
        "  inject holding [duration_min] [rate_lbs_hr]\n"
        "  inject notam [count]\n"
        "  clear [ias|alt_disagree|descent|windshear|energy|turbulence|fuel_leak|holding|notam]\n"
        "  clear   (clears all)\n"
        "  status"
    )

    while True:
        try:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                break
            parts = line.strip().split()
            if not parts:
                continue

            cmd = parts[0].lower()

            # ── inject ───────────────────────────────────────────────────────
            if cmd == "inject":
                if len(parts) < 2:
                    print("[CMD] inject needs a fault type — see help above")
                    continue

                fault = parts[1].lower()

                # Backward-compatible: 'inject captain -0.1' still means IAS
                if fault in ("captain", "fo", "both"):
                    side = fault
                    rate = float(parts[2]) if len(parts) > 2 else -0.1
                    manager.ias.start(side=side, rate=rate)

                elif fault == "ias":
                    side = parts[2] if len(parts) > 2 else "captain"
                    rate = float(parts[3]) if len(parts) > 3 else -0.1
                    manager.ias.start(side=side, rate=rate)

                elif fault == "alt_disagree":
                    side = parts[2] if len(parts) > 2 else "captain"
                    rate = float(parts[3]) if len(parts) > 3 else 2.0
                    manager.alt_disagree.start(side=side, rate=rate)

                elif fault == "descent":
                    rate_fpm = float(parts[2]) if len(parts) > 2 else 600.0
                    manager.descent.start(rate_fpm=rate_fpm)

                elif fault == "windshear":
                    rate_fpm  = float(parts[2]) if len(parts) > 2 else 4_000.0
                    duration  = float(parts[3]) if len(parts) > 3 else 15.0
                    manager.windshear.start(rate_fpm=rate_fpm, duration_s=duration)

                elif fault == "energy":
                    alt_fpm   = float(parts[2]) if len(parts) > 2 else 400.0
                    spd_kts_s = float(parts[3]) if len(parts) > 3 else 0.5
                    manager.energy.start(alt_rate_fpm=alt_fpm, spd_rate_kts_s=spd_kts_s)

                elif fault == "turbulence":
                    amp  = float(parts[2]) if len(parts) > 2 else 600.0
                    freq = float(parts[3]) if len(parts) > 3 else 0.25
                    manager.turbulence.start(amplitude_fpm=amp, freq_hz=freq)

                elif fault == "fuel_leak":
                    rate = float(parts[2]) if len(parts) > 2 else 400.0
                    tank = parts[3]        if len(parts) > 3 else "left"
                    manager.fuel_leak.start(rate_lbs_hr=rate, tank=tank)

                elif fault == "holding":
                    duration = float(parts[2]) * 60.0 if len(parts) > 2 else None
                    rate     = float(parts[3])         if len(parts) > 3 else None
                    manager.holding.start(gen, duration_s=duration, rate_lbs_hr=rate)

                elif fault == "notam":
                    count = int(parts[2]) if len(parts) > 2 else None
                    manager.notam.start(gen, count=count)

                else:
                    print(f"[CMD] Unknown fault type '{fault}'")

            # ── clear ────────────────────────────────────────────────────────
            elif cmd == "clear":
                target = parts[1].lower() if len(parts) > 1 else "all"
                _clear_map = {
                    "ias":          manager.ias,
                    "alt_disagree": manager.alt_disagree,
                    "descent":      manager.descent,
                    "windshear":    manager.windshear,
                    "energy":       manager.energy,
                    "turbulence":   manager.turbulence,
                    "fuel_leak":    manager.fuel_leak,
                    "holding":      manager.holding,
                    "notam":        manager.notam,
                }
                if target in _clear_map:
                    _clear_map[target].stop()
                else:
                    manager.clear_all()

            # ── status ───────────────────────────────────────────────────────
            elif cmd == "status":
                print(manager.status(gen))

            else:
                print(f"[CMD] Unknown command '{cmd}' — try 'inject', 'clear', or 'status'")

        except (EOFError, KeyboardInterrupt):
            break
        except Exception as exc:
            print(f"[CMD] Error: {exc}")


def _build_final_output(
    gen: FlightGenerator,
    jsonl_path: str,
    sim_dir: str,
    injected_emergency: str | None = None,
    notam_closures: list[dict] | None = None,
) -> str:
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
    }

    # Only add the key when an emergency was actually injected
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

    manager = InjectionManager()

    # 5 % chance of a random fault being injected automatically during flight
    if manager.configure_auto_inject(probability=0.05):
        print("[AERIS] Auto-inject ARMED — a fault will fire during flight")
    else:
        print("[AERIS] Auto-inject rolled clean — no automatic fault this flight")

    # 70 % chance this flight carries NOTAM airport/runway closures
    if manager.configure_notam_injection(probability=0.7):
        print("[AERIS] NOTAM injection ARMED — closures will be seeded pre-departure")
    else:
        print("[AERIS] NOTAM injection rolled clean — no closures this flight")

    gen     = FlightGenerator(perf, dt=1/30, emergency_fn=manager)
    ws      = WebSocketServer(host="0.0.0.0", port=8765)
    tracker = AlertTracker(ws)
    bus     = DataBus()
    register_all(bus, ws=tracker, perf=perf)

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
    cmd_task = asyncio.create_task(terminal_command_reader(manager, gen))

    await ws.broadcast_meta({
        "topic":              "flight_meta",
        "model":              perf.name,
        "origin_icao":        gen.origin["icao"],
        "destination_icao":   gen.dest["icao"],
        "route_distance_nm":  round(gen.route_distance_nm, 1),
        "departure_heading":  round(gen.departure_heading, 1),
        "landing_heading":    round(gen.landing_heading, 1),
    })

    with open(tmp_path, "w", encoding="utf-8") as log_fp:
        await flight_loop(gen, ws, log_fp, bus)

    ws_task.cancel()
    cmd_task.cancel()

    final = _build_final_output(
        gen, tmp_path, sim_dir, manager.injected_summary, manager.notam.closures,
    )
    print(f"[AERIS] Saved → {final}")

    if manager.injected_summary:
        print(f"[AERIS] Injected emergencies recorded: {manager.injected_summary}")

    if manager.notam.closures:
        icaos = ", ".join(c["icao"] for c in manager.notam.closures)
        print(f"[AERIS] NOTAM closures this flight: {icaos}")


def main():
    model = sys.argv[1] if len(sys.argv) > 1 else "c172p"
    asyncio.run(async_main(model))


if __name__ == "__main__":
    main()
