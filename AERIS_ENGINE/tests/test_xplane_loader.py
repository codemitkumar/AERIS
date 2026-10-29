"""X-Plane live source, tested against a fake X-Plane on a local UDP port.

    python -m unittest tests.test_xplane_loader -v
"""

import socket
import struct
import sys
import threading
import unittest
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from data.ingestion.flight_common import Phase  # noqa: E402
from data.ingestion import xplane_loader as xp  # noqa: E402


class FakeXPlane:
    """Answers every RREF request with the value for that dataref."""

    def __init__(self, values: dict[str, float]):
        self.values = values
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.running = True
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        refs = dict(xp._REFS)
        while self.running:
            try:
                data, addr = self.sock.recvfrom(1024)
            except (socket.timeout, OSError):
                continue
            hz, idx = struct.unpack_from("<ii", data, 5)
            if hz == 0:
                continue
            key = xp._REFS[idx][0]
            val = self.values.get(key, 0.0)
            assert refs[key]
            self.sock.sendto(b"RREF," + struct.pack("<if", idx, val), addr)

    def stop(self):
        self.running = False
        self.sock.close()


class PureFunctions(unittest.TestCase):
    def test_parse_rref(self):
        pkt = b"RREF," + struct.pack("<if", 3, 1.5) + struct.pack("<if", 7, -2.0)
        self.assertEqual(xp.parse_rref(pkt), {3: 1.5, 7: -2.0})
        self.assertEqual(xp.parse_rref(b"DATA*junk"), {})

    def test_perf_model_for(self):
        self.assertEqual(xp.perf_model_for("A333"), "A330-223")
        self.assertEqual(xp.perf_model_for("A20N"), "A320")
        self.assertEqual(xp.perf_model_for("B738"), "737")
        self.assertEqual(xp.perf_model_for("C172"), "c172p")
        self.assertEqual(xp.perf_model_for(""), "737")

    def test_flight_phase(self):
        self.assertEqual(xp.flight_phase(True, False, 0, 0, True), Phase.GROUND_ROLL)
        self.assertEqual(xp.flight_phase(True, True, 0, 0, True), Phase.LANDING)
        self.assertEqual(xp.flight_phase(False, True, 3000, 2500, False), Phase.CLIMB)
        self.assertEqual(xp.flight_phase(False, True, 30000, 0, False), Phase.CRUISE)
        self.assertEqual(xp.flight_phase(False, True, 9000, -1500, False), Phase.DESCENT)
        self.assertEqual(xp.flight_phase(False, True, 1500, -800, True), Phase.LANDING)


class LiveFeed(unittest.TestCase):
    def test_state_from_fake_xplane(self):
        values = {
            "lat": 47.8, "lon": 13.0, "heading": 150.0, "elev_m": 1000.0, "agl_m": 600.0,
            "ias_cap": 250.0, "ias_fo": 249.0, "alt_cap": 3300.0, "alt_fo": 3300.0,
            "vs_cap": 2500.0, "hdg_cap": 145.0, "baro_cap": 29.92, "baro_fo": 29.92,
            "slp_pa": 101325.0, "gs_ms": 130.0, "fuel_total": 55000.0,
            "fuel_left": 26000.0, "fuel_right": 26000.0, "n_engines": 2,
            "n1_0": 73.0, "n1_1": 72.5, "ff_0": 2.0, "ff_1": 2.0,
            "gear": 0.0, "on_ground": 0.0, "ap_servos": 1.0,
            "icao0": ord("A"), "icao1": ord("3"), "icao2": ord("3"), "icao3": ord("3"),
        }
        fake = FakeXPlane(values)
        try:
            live = xp.XPlaneLive(airports=[], port=fake.port, connect_timeout_s=3.0)
            state = live.step()
        finally:
            fake.stop()
            live.close()

        self.assertEqual(live.acf_icao, "A333")
        self.assertEqual(live.perf.model, "A330-223")
        self.assertEqual(state["phase"], "CLIMB")
        self.assertEqual(state["ias_fo"], 249.0)
        self.assertEqual(state["n1_pct"], [73.0, 72.5])
        self.assertAlmostEqual(state["groundspeed_kts"], 252.7, places=1)
        self.assertAlmostEqual(state["fuel_center_lbs"], 3000 * xp._KG_TO_LB, places=0)
        self.assertEqual(state["gear"], "UP")
        self.assertEqual(state["ap_mode"], "CLB")

    def test_no_xplane_raises(self):
        with self.assertRaises(ConnectionError):
            xp.XPlaneLive(airports=[], port=9, connect_timeout_s=1.0)


if __name__ == "__main__":
    unittest.main()
