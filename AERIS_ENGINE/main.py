import time
import jsbsim
import os
import subprocess

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def launch_flightgear():
    bat_path = os.path.join(BASE_DIR, "start_flightgear.bat")
    subprocess.Popen(["cmd", "/c", bat_path], creationflags=subprocess.CREATE_NEW_CONSOLE)
    print("FlightGear launching... waiting 15 seconds for it to load.")
    time.sleep(120)


def init_jsbsim():
    fdm = jsbsim.FGFDMExec(None)
    fdm.set_debug_level(0)
    fdm.load_model("c172p")

    fg_xml_path = os.path.join(BASE_DIR, "flightgear.xml")
    fdm.set_output_directive(fg_xml_path)

    fdm.set_dt(1.0 / 60.0)

    fdm["ic/lat-gc-deg"] = 28.56
    fdm["ic/long-gc-deg"] = 77.10
    fdm["ic/h-sl-ft"] = 3000
    fdm["ic/u-fps"] = 200
    fdm["ic/v-fps"] = 0
    fdm["ic/w-fps"] = 0
    fdm["ic/phi-deg"] = 0
    fdm["ic/theta-deg"] = 2
    fdm["ic/psi-true-deg"] = 90

    fdm["propulsion/set-running"] = -1
    fdm.run_ic()

    return fdm


def main():
    launch_flightgear()

    fdm = init_jsbsim()
    print("JSBSim running. Streaming flight data to FlightGear on UDP port 5500...")

    step = 0
    while True:
        # Set controls before stepping the simulation
        fdm["fcs/throttle-cmd-norm"] = 0.6
        fdm["fcs/elevator-cmd-norm"] = 0.0
        fdm["fcs/aileron-cmd-norm"] = 0.0
        fdm["fcs/rudder-cmd-norm"] = 0.0

        fdm.run()

        step += 1
        if step % 60 == 0:  # Print once per second
            print(
                f"ALT: {fdm['position/h-sl-ft']:.1f} ft | "
                f"SPD: {fdm['velocities/vtrue-kts']:.1f} kts | "
                f"HDG: {fdm['attitude/psi-deg']:.1f} deg"
            )

        time.sleep(1.0 / 60.0)


if __name__ == "__main__":
    main()
