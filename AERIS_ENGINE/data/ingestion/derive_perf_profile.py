"""Derive an aircraft performance profile from recorded flights.

The DASHlink archive does not say what aircraft it is, and running it against
a stock profile for a different type is what produced most of the false alarms
on a clean flight. So measure the airframe from its own recordings instead.

Values come either from a channel the aircraft records directly (its own
max-allowable airspeed, its own N1 range) or from what it actually did across
many flights. Things normal operations cannot reveal - glide ratio, Va,
structural limits - are left alone and listed at the end.

    python -m data.ingestion.derive_perf_profile <dir-of-mat-files> [--json out.json]
"""

import argparse
import glob
import json
import os
import statistics as stats

from data.ingestion.dashlink_loader import DashlinkFlight, Phase


# An observed maximum is not a certified limit. Without the type certificate
# the real redline is unknown, so thresholds sit a margin above observed-normal.
# That detects departure from this fleet's own behaviour, which is the more
# useful signal anyway.
EGT_EXCEEDANCE_MARGIN_C = 60.0


def _pct(values, p):
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(p / 100.0 * (len(ordered) - 1)))))
    return float(ordered[idx])


def _phase_samples(flight: DashlinkFlight, channel: str, phase: Phase):
    """Every sample of `channel` recorded while the flight was in `phase`."""
    if not flight.live(channel):
        return []
    c = flight.channels[channel]
    rate = c["rate"]
    out = []
    for i, v in enumerate(c["data"]):
        if flight.phase_at(i / rate) is phase:
            out.append(float(v))
    return out


def analyse_flight(path: str) -> dict:
    f = DashlinkFlight(path)
    r = {"file": f.name, "engines": f.engine_count}

    cruise_alt = _phase_samples(f, "ALT", Phase.CRUISE)
    r["cruise_alt_ft"] = stats.median(cruise_alt) if cruise_alt else 0.0

    # The aircraft records its own max-allowable airspeed.
    if f.live("CASM"):
        r["vmo_kts"] = float(max(f.channels["CASM"]["data"]))
    r["mach_max_seen"] = float(max(f.channels["MACH"]["data"])) if f.live("MACH") else 0.0

    climb_cas = _phase_samples(f, "CAS", Phase.CLIMB)
    r["climb_speed_kts"] = _pct(climb_cas, 75) if climb_cas else 0.0

    # VREF is a floor, not a typical speed - crews fly VREF plus an additive.
    # The median would put the aircraft below its own VREF half the time.
    if f.live("CAS") and f.live("RALT"):
        cas_c, ra_c = f.channels["CAS"], f.channels["RALT"]
        finals = []
        for i, ra in enumerate(ra_c["data"]):
            if 50.0 < ra < 500.0:
                t = i / ra_c["rate"]
                j = int(t * cas_c["rate"])
                if j < len(cas_c["data"]):
                    finals.append(float(cas_c["data"][j]))
        r["vref_kts"] = _pct(finals, 5) if finals else 0.0

    # Samples below 5% are shut-down engines, not idle.
    n1_all = []
    for i in range(1, f.engine_count + 1):
        if f.live(f"N1_{i}"):
            n1_all.extend(v for v in (float(x) for x in f.channels[f"N1_{i}"]["data"])
                          if v > 5.0)
    if n1_all:
        r["n1_idle_pct"] = _pct(n1_all, 2)
        r["n1_toga_pct"] = _pct(n1_all, 99.9)

    for phase, key in ((Phase.CRUISE, "ff_cruise_lbs_hr"),
                       (Phase.CLIMB, "ff_climb_lbs_hr"),
                       (Phase.DESCENT, "ff_descent_lbs_hr"),
                       (Phase.GROUND_ROLL, "ff_taxi_lbs_hr")):
        per_eng = []
        for i in range(1, f.engine_count + 1):
            s = _phase_samples(f, f"FF_{i}", phase)
            if s:
                per_eng.append(stats.median(s))
        r[key] = stats.mean(per_eng) if per_eng else 0.0

    # Operating envelope, not a certified limit - see EGT_EXCEEDANCE_MARGIN_C.
    for phase, key in ((Phase.CLIMB, "egt_normal_climb_c"),
                       (Phase.CRUISE, "egt_normal_cruise_c")):
        peaks = []
        for i in range(1, f.engine_count + 1):
            s = _phase_samples(f, f"EGT_{i}", phase)
            if s:
                peaks.append(_pct(s, 99.9))
        r[key] = max(peaks) if peaks else 0.0

    tanks_total = 0.0
    for i in range(1, 9):
        if f.live(f"FQTY_{i}"):
            tanks_total += float(max(f.channels[f"FQTY_{i}"]["data"]))
    r["fuel_observed_max_lbs"] = tanks_total

    climb_pitch = _phase_samples(f, "PTCH", Phase.CLIMB)
    r["climb_pitch_deg"] = stats.median(climb_pitch) if climb_pitch else 0.0
    r["rotation_pitch_deg"] = _pct(climb_pitch, 95) if climb_pitch else 0.0

    r["duration_min"] = f.duration_s / 60.0
    return r


def aggregate(results: list) -> dict:
    """Limits take the extreme across flights, since an airframe's VMO is the
    same whichever flight you watch. Operating values take the median so one
    unusual flight cannot move them."""
    def med(key):
        vals = [r[key] for r in results if r.get(key)]
        return stats.median(vals) if vals else 0.0

    def hi(key):
        vals = [r[key] for r in results if r.get(key)]
        return max(vals) if vals else 0.0

    return {
        "flights_analysed":   len(results),
        "engine_count":       int(med("engines")),
        "cruise_alt_ft":      round(med("cruise_alt_ft"), -2),
        "vmo_kts":            round(hi("vmo_kts"), 1),
        "mach_max_seen":      round(hi("mach_max_seen"), 3),
        "climb_speed_kts":    round(med("climb_speed_kts"), 1),
        "vref_kts":           round(min([r["vref_kts"] for r in results if r.get("vref_kts")] or [0]), 1),
        "n1_idle_pct":        round(med("n1_idle_pct"), 1),
        "n1_toga_pct":        round(hi("n1_toga_pct"), 1),
        "ff_cruise_lbs_hr":   round(med("ff_cruise_lbs_hr"), 0),
        "ff_climb_lbs_hr":    round(med("ff_climb_lbs_hr"), 0),
        "ff_descent_lbs_hr":  round(med("ff_descent_lbs_hr"), 0),
        "ff_taxi_lbs_hr":     round(med("ff_taxi_lbs_hr"), 0),
        "egt_normal_climb_c":  round(hi("egt_normal_climb_c"), 0),
        "egt_normal_cruise_c": round(hi("egt_normal_cruise_c"), 0),
        "egt_limit_climb_c":   round(hi("egt_normal_climb_c") + EGT_EXCEEDANCE_MARGIN_C, 0),
        "egt_limit_cruise_c":  round(hi("egt_normal_cruise_c") + EGT_EXCEEDANCE_MARGIN_C, 0),
        "egt_limit_toga_c":    round(hi("egt_normal_climb_c") + EGT_EXCEEDANCE_MARGIN_C, 0),
        "fuel_capacity_lbs":  round(hi("fuel_observed_max_lbs"), -2),
        "climb_pitch_deg":    round(med("climb_pitch_deg"), 1),
        "rotation_pitch_deg": round(med("rotation_pitch_deg"), 1),
        "median_duration_min": round(med("duration_min"), 1),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("directory", help="directory of extracted .mat flights")
    ap.add_argument("--json", help="write the derived profile here")
    ap.add_argument("--limit", type=int, default=0, help="cap flights analysed")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.directory, "*.mat")))
    if args.limit:
        files = files[:args.limit]
    if not files:
        raise SystemExit(f"no .mat files in {args.directory}")

    results = []
    for path in files:
        try:
            results.append(analyse_flight(path))
            print(f"  analysed {os.path.basename(path)}")
        except Exception as exc:
            print(f"  skipped  {os.path.basename(path)}: {exc}")

    if not results:
        raise SystemExit("no flights could be analysed")

    profile = aggregate(results)

    print("\nDerived profile")
    print("-" * 46)
    for k, v in profile.items():
        print(f"  {k:<22} {v}")

    print("\nNot derivable from normal operations — left at defaults:")
    for name in ("va_kts", "vfe_kts", "vle_kts", "vlo_kts",
                 "best_glide_kts", "glide_ratio", "tow_lbs", "wing_area_ft2"):
        print(f"  {name}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"profile": profile, "per_flight": results}, fh, indent=2)
        print(f"\nwritten to {args.json}")


if __name__ == "__main__":
    main()
