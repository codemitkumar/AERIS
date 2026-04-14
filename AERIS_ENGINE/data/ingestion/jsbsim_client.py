import jsbsim

class JSBSimClient:
    def __init__(self):
        self.fdm = jsbsim.FGFDMExec(None)
        self.fdm.set_debug_level(0)
        self.fdm.load_model("c172p")  # load model FIRST, then set ICs

        # Start on the ground at VIDP runway 10/28, elevation ~777 ft MSL
        self.fdm["ic/lat-gc-deg"] = 28.5665
        self.fdm["ic/long-gc-deg"] = 77.1031
        self.fdm["ic/h-sl-ft"] = 777
        self.fdm["ic/vt-kts"] = 0
        self.fdm["ic/psi-true-deg"] = 280   # runway 28 heading

        self.fdm.set_dt(0.01)               # 100 Hz simulation
        self.fdm.run_ic()

    def step(self):
        self.fdm.run()

    def get_state(self):
        return {
        "lat": self.fdm["position/lat-gc-deg"],
        "lon": self.fdm["position/long-gc-deg"],
        "altitude": self.fdm["position/h-sl-ft"],
        "airspeed": self.fdm["velocities/vtrue-kts"],
        "pitch": self.fdm["attitude/pitch-rad"],
        "roll": self.fdm["attitude/roll-rad"],
        "heading": self.fdm["attitude/heading-true-rad"],
        "fuel": self.fdm["propulsion/total-fuel-lbs"],
        "throttle": self.fdm["fcs/throttle-cmd-norm"],
    }
    def set_controls(self, throttle=0.7, elevator=0.0, flaps=0.0):
        self.fdm["fcs/throttle-cmd-norm"] = throttle
        self.fdm["fcs/elevator-cmd-norm"] = elevator
        self.fdm["fcs/flap-cmd-norm"] = flaps