"""NTSB docket regression tests.

Two layers:

  * Unit tests (always run, no data needed): CSV parsing, sentinel handling,
    air-data maths, the tail-strike monitor and GRACE's return-to-field rule.

  * Case replays: every tests/ntsb_cases/<ID>.json whose FDR CSV is present
    under data/ntsb/<ID>/ is replayed through the full module stack and
    checked against its "expect" block. Missing data skips, it doesn't fail —
    fetch it with scripts/fetch_ntsb_docket.py.

    "known_false_alarms" are module limitations real data exposes. They're
    excluded from the silent-window checks and asserted in an
    expectedFailure test, so fixing one shows up as an unexpected success —
    the prompt to delete its entry.

Run from AERIS_ENGINE:

    python -m unittest tests.test_ntsb_cases -v
"""

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from data.ingestion.aircraft_performance import get_performance  # noqa: E402
from data.ingestion.ntsb_fdr_loader import (  # noqa: E402
    NtsbFlight, NtsbReplay, mach_from_cas, tas_from_mach)
from decision.diversion_selector import DiversionSelector  # noqa: E402
from modules.math.attitude.tail_strike import TailStrikeModule  # noqa: E402
from tests import ntsb_harness as harness  # noqa: E402


# ── unit tests ───────────────────────────────────────────────────────────────

_MINI_CSV = """NTSB TEST01, Attachment 1 to Flight Data Recorder - Specialist's Factual Report
Operator: Test Air
Source of Data: Boeing 757-200
DATA
Time,Air Grnd,Airspeed Comp,Altitude Press,Altitude Radio,Ground Spd,Pitch,Latitude,Longitude,AP Cmd-R
(EDT),(),(kts),(ft),(ft),(kts),(deg),(deg),(deg),()
,"%N(0:0=""Air"",1:1=""Grnd"")",NUMBER,NUMBER,NUMBER,NUMBER,NUMBER,NUMBER,NUMBER,"%N(0:0=""Off"",1:1=""On"")"
23:59:58.000000,Grnd,384.0,700,-4,5,0.5,33.64,-84.43,Off
23:59:59.000000,,0.0,,,,,,,
00:00:00.000000,Grnd,120.0,700,-4,118,9.0,33.64,-84.42,Off
00:00:01.000000,Air,140.0,720,6,138,12.0,33.64,-84.41,
00:00:02.000000,Air,145.0,760,40,142,14.0,33.64,-84.40,On
"""


def _write_csv(text: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(fd, "w", encoding="latin-1", newline="") as f:
        f.write(text)
    return path


class ParserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = _write_csv(_MINI_CSV)
        cls.flight = NtsbFlight(cls.path)

    @classmethod
    def tearDownClass(cls):
        os.remove(cls.path)

    def test_metadata_and_channels(self):
        f = self.flight
        self.assertEqual(f.meta.get("Operator"), "Test Air")
        for canon in ("air_ground", "cas", "pressure_alt", "radio_alt", "gs", "pitch", "lat"):
            self.assertIn(canon, f.channels)

    def test_discretes_decode_from_encoding_row(self):
        ag = self.flight.channels["air_ground"]
        self.assertEqual(ag.true_text, "grnd")
        self.assertEqual(ag.v, [1.0, 1.0, 0.0, 0.0])

    def test_midnight_rollover_keeps_time_monotonic(self):
        t = self.flight.channels["cas"].t
        self.assertEqual(t, sorted(t))
        self.assertAlmostEqual(self.flight.duration_s, 4.0)

    def test_sparse_rows_hold_last_sample(self):
        # 23:59:59 row has only CAS; altitude holds the 23:59:58 value.
        alt = self.flight.channels["pressure_alt"]
        self.assertEqual(alt.at(self.flight.t0 + 1.5), 700.0)

    def test_air_intervals_from_discrete(self):
        iv = self.flight.air_intervals
        self.assertEqual(len(iv), 1)
        self.assertAlmostEqual(iv[0][0] - self.flight.t0, 3.0)

    def test_ground_cas_sentinel_zeroed(self):
        perf = get_performance("B757-200")
        replay = NtsbReplay(self.flight, perf, rate_hz=1.0)
        first = replay.step()
        self.assertEqual(first["ias_captain"], 0.0)          # 384 kt at 5 kt GS
        replay.step(); replay.step()
        airborne = replay.step()
        self.assertEqual(airborne["ias_captain"], 140.0)
        self.assertEqual(airborne["weight_on_wheels"], 0)
        replay.step()


class AirDataTest(unittest.TestCase):
    def test_mach_from_cas_matches_isa_tables(self):
        self.assertAlmostEqual(mach_from_cas(250.0, 0.0), 0.378, places=3)
        self.assertAlmostEqual(mach_from_cas(280.0, 35000.0), 0.826, places=2)

    def test_tas_isa_at_altitude(self):
        m = mach_from_cas(280.0, 35000.0)
        self.assertAlmostEqual(tas_from_mach(m, 35000.0), 474.0, delta=3.0)


class _Sink:
    def __init__(self):
        self.payloads = []

    async def broadcast_alert(self, p):
        self.payloads.append(p)


def _tail_state(pitch, wow=1, ra=-3.0, gs=130.0, phase="LANDING", t=0.0):
    return {"phase": phase, "pitch_deg": pitch, "weight_on_wheels": wow,
            "radio_alt_ft": ra, "groundspeed_kts": gs, "ias_captain": gs,
            "time": t, "dt": 0.25, "sample_rate_hz": 4.0}


class TailStrikeTest(unittest.TestCase):
    def setUp(self):
        self.sink = _Sink()
        self.mod = TailStrikeModule(ws=self.sink, perf=get_performance("B757-200"))

    def _run(self, states):
        async def go():
            for s in states:
                await self.mod.on_state(s)
        asyncio.run(go())
        return [(p["topic"], p.get("severity")) for p in self.sink.payloads]

    def test_over_rotation_on_runway_is_critical_and_sticks(self):
        seq = [_tail_state(11.6, t=0.0)] + [
            _tail_state(3.0, wow=0, ra=400.0, phase="CLIMB", t=1 + i * 0.25) for i in range(40)]
        self.assertEqual(self._run(seq), [("alert", "critical")])

    def test_normal_liftoff_attitude_is_quiet(self):
        seq = [_tail_state(p, wow=w, ra=r, phase=ph, t=i * 0.25)
               for i, (p, w, r, ph) in enumerate([
                   (4.6, 1, -3.0, "GROUND_ROLL"), (8.1, 0, 0.0, "CLIMB"),
                   (9.5, 0, 5.0, "CLIMB"), (15.1, 0, 37.0, "CLIMB")])]
        self.assertEqual(self._run(seq), [])

    def test_high_pitch_airborne_is_not_a_tail_strike(self):
        self.assertEqual(self._run([_tail_state(14.0, wow=0, ra=300.0, phase="CLIMB")]), [])

    def test_unknown_geometry_disables_monitor(self):
        mod = TailStrikeModule(ws=self.sink, perf=get_performance("A320"))
        asyncio.run(mod.on_state(_tail_state(14.0)))
        self.assertEqual(self.sink.payloads, [])


class GraceReturnToFieldTest(unittest.TestCase):
    """After a tail strike on the go-around, the field behind is still the
    nearest suitable airport — GRACE must not prefer one ahead."""

    AIRPORTS = [
        {"icao": "KATL", "lat": 33.6367, "lon": -84.4279, "airport_type": "large_airport",
         "max_rwy_ft": 12390, "runways": [{"id": "09R/27L", "length_ft": 9000}] * 5},
        {"icao": "KCVC", "lat": 33.6323, "lon": -83.8488, "airport_type": "medium_airport",
         "max_rwy_ft": 5500, "runways": [{"id": "10/28", "length_ft": 5500}]},
    ]

    def _state(self):
        # 3 nm east of ATL heading east, climbing out of the go-around.
        return {"lat": 33.637, "lon": -84.37, "heading_deg": 95.0,
                "groundspeed_kts": 180.0, "fuel_total_lbs": 0.0,
                "fuel_flow_total_lbs_hr": 0.0, "fuel_reserve_min_lbs": 0.0,
                "origin_icao": "KFLL", "destination_icao": "KATL", "time": 6600.0}

    def test_tail_strike_returns_to_field(self):
        picks = DiversionSelector().select(
            self._state(), self.AIRPORTS, vref_kts=135.0, severity="HIGH",
            active_conditions=["TAIL_STRIKE"])
        self.assertEqual(picks[0]["icao"], "KATL")
        self.assertEqual(picks[0]["anchor"], "return_to_field")

    def test_without_condition_the_field_behind_loses(self):
        # Shows the rule is what makes the difference, not the geometry.
        picks = DiversionSelector().select(
            self._state(), self.AIRPORTS, vref_kts=135.0, severity="HIGH",
            active_conditions=[])
        self.assertEqual(picks[0]["icao"], "KCVC")


# ── case replays ─────────────────────────────────────────────────────────────

_RESULTS: dict = {}


def _result(case_id):
    if case_id not in _RESULTS:
        _RESULTS[case_id] = harness.run_case(case_id)
    return _RESULTS[case_id]


def _window(spec):
    return harness.to_seconds(spec["between"][0]), harness.to_seconds(spec["between"][1])


def _known(fixture):
    return [(k["id"], *_window(k)) for k in fixture.get("known_false_alarms", [])]


def _is_known(event, known):
    return any(event.id == kid and a <= event.t <= b for kid, a, b in known)


class _CaseChecks:
    """Mixed into one TestCase per fixture by _make_case_tests()."""
    case_id = ""

    @classmethod
    def setUpClass(cls):
        if not harness.data_available(cls.case_id):
            raise unittest.SkipTest(
                f"{cls.case_id}: no FDR CSV under data/ntsb/{cls.case_id} — "
                f"run scripts/fetch_ntsb_docket.py")
        cls.fixture = harness.load_fixture(cls.case_id)
        cls.res = _result(cls.case_id)

    def test_expected_alerts_fire(self):
        for exp in self.fixture["expect"].get("alerts", []):
            a, b = _window(exp)
            with self.subTest(alert=exp["id"], window=exp["between"]):
                hits = self.res.raised(exp["id"], a, b, exp.get("severity"))
                self.assertTrue(hits, f"{exp['id']} not raised in {exp['between']}: {exp['why']}")

    def test_silent_windows(self):
        known = _known(self.fixture)
        for exp in self.fixture["expect"].get("silent", []):
            a, b = _window(exp)
            aid = None if exp["id"] == "*" else exp["id"]
            with self.subTest(alert=exp["id"], window=exp["between"]):
                noise = [e for e in self.res.raised(aid, a, b) if not _is_known(e, known)]
                self.assertFalse(
                    noise, f"{exp['why']}\n  unexpected: " +
                    ", ".join(f"{e.clock} {e.id}" for e in noise[:10]))

    def test_grace_picks(self):
        for exp in self.fixture["expect"].get("grace", []):
            a, b = _window(exp)
            with self.subTest(window=exp["between"], top=exp["top_icao"]):
                recs = [r for r in self.res.recommendations if a <= r[0] <= b]
                self.assertTrue(recs, f"GRACE made no recommendation in {exp['between']}")
                wrong = [(clock, r["candidates"][0]["icao"] if r["candidates"] else None)
                         for _, clock, _, r in recs
                         if not r["candidates"] or r["candidates"][0]["icao"] != exp["top_icao"]]
                self.assertFalse(wrong, f"{exp['why']}\n  top pick differed: {wrong[:10]}")

    @unittest.expectedFailure
    def test_known_false_alarms_fixed(self):
        """Fails while any known module limitation still fires. When this
        reports an unexpected success, remove the fixed entries."""
        still = [k[0] for k in _known(self.fixture) if self.res.raised(k[0], k[1], k[2])]
        self.assertFalse(still, f"still firing: {sorted(set(still))}")


def _make_case_tests():
    for case_id in harness.list_cases():
        name = f"Case_{case_id}"
        globals()[name] = type(name, (_CaseChecks, unittest.TestCase), {"case_id": case_id})


_make_case_tests()


if __name__ == "__main__":
    unittest.main()
