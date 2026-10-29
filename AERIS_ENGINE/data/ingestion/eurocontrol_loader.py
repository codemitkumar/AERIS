"""EUROCONTROL R&D data loader.

Reads the sample-month CSVs under <AERIS_DATA_ROOT>/adrrData/<yyyymm>/
(flight plans, FIR/UIR airspace polygons, ATS route network) per the schema
in "FNI025 R&D Data Description v10.pdf". R&D-use-only license — see
README.md Section 7.4/10. Not distributed with this repository; lives on
the external research-data drive (config/settings.py), not under
AERIS_ENGINE/.
"""

import csv
import glob
import math
from pathlib import Path

from config.settings import ADRR_DIR as _ADRR_DIR
from config.settings import require_data_root

DEFAULT_MONTH = "202406"

_flights_cache: dict[str, list[dict]] = {}
_fir_cache: dict[str, dict] = {}
_route_cache: dict[str, dict] = {}


def _float(val: str):
    try:
        return round(float(val), 5) if val and str(val).strip() else None
    except ValueError:
        return None


def _month_dir(month: str) -> Path:
    require_data_root()  # distinct error if the drive itself isn't connected
    d = _ADRR_DIR / month
    if not d.is_dir():
        raise FileNotFoundError(
            f"No EUROCONTROL data directory for month {month!r} at {d}"
        )
    return d


def load_flights(month: str = DEFAULT_MONTH) -> list[dict]:
    """Load real flight plans for a sample month.

    Returns a list of dicts: ectl_id, adep, adep_lat, adep_lon, ades,
    ades_lat, ades_lon, ac_type, ac_operator, icao_flight_type,
    requested_fl, distance_flown_nm.
    """
    if month in _flights_cache:
        return _flights_cache[month]

    d = _month_dir(month)
    matches = glob.glob(str(d / "Flights_*.csv"))
    if not matches:
        raise FileNotFoundError(f"No Flights_*.csv found under {d}")

    result: list[dict] = []
    with open(matches[0], encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            adep_lat = _float(row.get("ADEP Latitude", ""))
            adep_lon = _float(row.get("ADEP Longitude", ""))
            ades_lat = _float(row.get("ADES Latitude", ""))
            ades_lon = _float(row.get("ADES Longitude", ""))
            if None in (adep_lat, adep_lon, ades_lat, ades_lon):
                continue

            result.append({
                "ectl_id":           row.get("ECTRL ID", "").strip(),
                "adep":              row.get("ADEP", "").strip(),
                "adep_lat":          adep_lat,
                "adep_lon":          adep_lon,
                "ades":              row.get("ADES", "").strip(),
                "ades_lat":          ades_lat,
                "ades_lon":          ades_lon,
                "ac_type":           row.get("AC Type", "").strip(),
                "ac_operator":       row.get("AC Operator", "").strip(),
                "icao_flight_type":  row.get("ICAO Flight Type", "").strip(),
                "requested_fl":      _float(row.get("Requested FL", "")),
                "distance_flown_nm": _float(row.get("Actual Distance Flown (nm)", "")),
            })

    _flights_cache[month] = result
    return result


def load_fir_polygons(month: str = DEFAULT_MONTH) -> dict[str, dict]:
    """Load real FIR/UIR airspace boundary polygons for a sample month.

    A month directory typically has two FIR_<AIRAC>.csv files (the AIRAC
    cycle active at the start and end of the month) — both are merged;
    later cycle wins per airspace ID since structure is largely stable
    within a month.

    Returns {airspace_id: {min_fl, max_fl, points: [(lat, lon), ...]}}.
    """
    if month in _fir_cache:
        return _fir_cache[month]

    d = _month_dir(month)
    files = sorted(glob.glob(str(d / "FIR_*.csv")))
    if not files:
        raise FileNotFoundError(f"No FIR_*.csv found under {d}")

    polygons: dict[str, dict] = {}
    for path in files:
        by_airspace: dict[str, list[tuple[int, float, float]]] = {}
        min_max: dict[str, tuple[float, float]] = {}
        with open(path, encoding="utf-8", errors="replace") as f:
            for row in csv.DictReader(f):
                aid = row.get("Airspace ID", "").strip()
                lat = _float(row.get("Latitude", ""))
                lon = _float(row.get("Longitude", ""))
                seq = row.get("Sequence Number", "")
                if not aid or lat is None or lon is None:
                    continue
                try:
                    seq_n = int(seq)
                except ValueError:
                    continue
                by_airspace.setdefault(aid, []).append((seq_n, lat, lon))
                min_fl = _float(row.get("Min Flight Level", "")) or 0.0
                max_fl = _float(row.get("Max Flight Level", "")) or 999.0
                min_max[aid] = (min_fl, max_fl)

        for aid, pts in by_airspace.items():
            pts.sort(key=lambda p: p[0])
            min_fl, max_fl = min_max[aid]
            polygons[aid] = {
                "min_fl": min_fl,
                "max_fl": max_fl,
                "points": [(lat, lon) for _, lat, lon in pts],
            }

    _fir_cache[month] = polygons
    return polygons


def load_routes(month: str = DEFAULT_MONTH) -> dict[str, list[tuple[float, float]]]:
    """Load the real published ATS airway network for a sample month.

    Merges the (typically two) Route_<AIRAC>.csv files present per month.
    Returns {route_id: [(lat, lon), ...]} ordered by sequence number.
    """
    if month in _route_cache:
        return _route_cache[month]

    d = _month_dir(month)
    files = sorted(glob.glob(str(d / "Route_*.csv")))
    if not files:
        raise FileNotFoundError(f"No Route_*.csv found under {d}")

    by_route: dict[str, list[tuple[int, float, float]]] = {}
    for path in files:
        with open(path, encoding="utf-8", errors="replace") as f:
            for row in csv.DictReader(f):
                rid = row.get("Route ID", "").strip()
                lat = _float(row.get("Latitude", ""))
                lon = _float(row.get("Longitude", ""))
                seq = row.get("Sequence Number", "")
                if not rid or lat is None or lon is None:
                    continue
                try:
                    seq_n = int(seq)
                except ValueError:
                    continue
                by_route.setdefault(rid, []).append((seq_n, lat, lon))

    result = {}
    for rid, pts in by_route.items():
        pts.sort(key=lambda p: p[0])
        result[rid] = [(lat, lon) for _, lat, lon in pts]

    _route_cache[month] = result
    return result


def fir_for_point(lat: float, lon: float, polygons: dict[str, dict]) -> str | None:
    """Return the airspace ID whose boundary polygon contains (lat, lon), or
    None. Standard ray-casting point-in-polygon test; first match wins when
    airspaces overlap (upper/lower FIR pairs share ground track)."""
    for aid, poly in polygons.items():
        pts = poly["points"]
        if len(pts) < 3:
            continue
        if _point_in_polygon(lat, lon, pts):
            return aid
    return None


def _point_in_polygon(lat: float, lon: float, pts: list[tuple[float, float]]) -> bool:
    inside = False
    n = len(pts)
    j = n - 1
    for i in range(n):
        yi, xi = pts[i]
        yj, xj = pts[j]
        if ((yi > lat) != (yj > lat)) and (
            lon < (xj - xi) * (lat - yi) / (yj - yi + 1e-12) + xi
        ):
            inside = not inside
        j = i
    return inside


def nearest_route_between(
    lat1: float, lon1: float, lat2: float, lon2: float,
    routes: dict[str, list[tuple[float, float]]],
    max_endpoint_nm: float = 120.0,
) -> list[tuple[float, float]] | None:
    """Find a published route whose endpoints roughly match (lat1,lon1) and
    (lat2,lon2), for drawing a realistic-looking route line — a simple
    nearest-endpoint heuristic, not a routing/graph solver (real IFR routing
    typically chains several airways, which this does not attempt).

    Returns the route's waypoint list oriented start-to-end, or None if no
    route has both endpoints within `max_endpoint_nm` of the requested pair.
    """
    best = None
    best_cost = max_endpoint_nm * 2
    for wps in routes.values():
        if len(wps) < 2:
            continue
        start, end = wps[0], wps[-1]

        d_fwd = _haversine_nm(lat1, lon1, *start) + _haversine_nm(lat2, lon2, *end)
        if d_fwd < best_cost:
            best_cost, best = d_fwd, wps

        d_rev = _haversine_nm(lat1, lon1, *end) + _haversine_nm(lat2, lon2, *start)
        if d_rev < best_cost:
            best_cost, best = d_rev, list(reversed(wps))

    return best


def _haversine_nm(lat1, lon1, lat2, lon2) -> float:
    R = 3440.065
    lat1r, lat2r = math.radians(lat1), math.radians(lat2)
    dlat = lat2r - lat1r
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1r) * math.cos(lat2r) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))
