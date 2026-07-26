"""FAA NASR airport loader.

Reads APT_BASE.csv + APT_RWY.csv from AERIS_ENGINE/utils/ and returns a list
of airport dicts classified as large_airport / medium_airport / small_airport.

Classification rules (derived from FAA NASR field definitions):
  large_airport  — FAR-139 certified (Part 139) + scheduled commercial ops
                   + longest paved runway >= 7 000 ft
  medium_airport — commuter or based jet activity + jet fuel + paved RWY >= 4 000 ft
  small_airport  — all other public, operational, paved airports
"""

import csv
from pathlib import Path

_UTILS_DIR   = Path(__file__).resolve().parent.parent.parent / "utils"
_BASE_CSV    = _UTILS_DIR / "APT_BASE.csv"
_RWY_CSV     = _UTILS_DIR / "APT_RWY.csv"
_RWY_END_CSV = _UTILS_DIR / "APT_RWY_END.csv"

_cache: list[dict] | None = None


def _float(val: str):
    try:
        return round(float(val), 5) if val and str(val).strip() else None
    except ValueError:
        return None


def _int(val: str, default: int = 0) -> int:
    if not val or not str(val).strip():
        return default
    try:
        return int(str(val).strip())
    except ValueError:
        # Some numeric NASR fields (e.g. ELEV) are decimal strings ("463.1").
        try:
            return round(float(str(val).strip()))
        except ValueError:
            return default


def _has_jet_fuel(fuel_types: str) -> bool:
    tokens = fuel_types.replace(",", " ").upper().split()
    return any(t in ("A", "A+", "AB", "JETA", "JETB") for t in tokens)


def _build_rwy_index(path: Path) -> dict[str, tuple[int, bool]]:
    """Return {arpt_id: (max_rwy_ft, has_paved)} from APT_RWY.csv."""
    idx: dict[str, tuple[int, bool]] = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            aid = row.get("ARPT_ID", "").strip()
            if not aid:
                continue
            length  = _int(row.get("RWY_LEN", ""))
            surface = row.get("SURFACE_TYPE_CODE", "").strip().upper()
            paved   = "ASPH" in surface or "CONC" in surface
            prev_len, prev_paved = idx.get(aid, (0, False))
            idx[aid] = (max(prev_len, length), prev_paved or paved)
    return idx


def _build_runway_ends(path: Path) -> dict:
    """Return {(arpt_id, rwy_id): {rwy_end_id: {id, heading_true, lat, lon, elev_ft}}}."""
    ends: dict = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            aid = row.get("ARPT_ID", "").strip()
            rid = row.get("RWY_ID", "").strip()
            eid = row.get("RWY_END_ID", "").strip()
            if not aid or not rid or not eid:
                continue

            lat = _float(row.get("LAT_DECIMAL", ""))
            lon = _float(row.get("LONG_DECIMAL", ""))
            if lat is None or lon is None:
                continue

            ends.setdefault((aid, rid), {})[eid] = {
                "id":           eid,
                "heading_true": _float(row.get("TRUE_ALIGNMENT", "")),
                "lat":          lat,
                "lon":          lon,
                "elev_ft":      _float(row.get("RWY_END_ELEV", "")),
            }
    return ends


def _build_runways(rwy_path: Path, rwy_end_path: Path) -> dict:
    """Return {arpt_id: [runway dicts]} joining APT_RWY.csv + APT_RWY_END.csv."""
    rwy_ends = _build_runway_ends(rwy_end_path)
    runways: dict = {}
    with open(rwy_path, encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            aid = row.get("ARPT_ID", "").strip()
            rid = row.get("RWY_ID", "").strip()
            if not aid or not rid:
                continue

            ends_map = rwy_ends.get((aid, rid), {})
            if len(ends_map) < 2:
                # Need both ends to know the runway's true orientation.
                continue

            runways.setdefault(aid, []).append({
                "id":        rid,
                "length_ft": _int(row.get("RWY_LEN", "")),
                "width_ft":  _int(row.get("RWY_WIDTH", "")),
                "surface":   row.get("SURFACE_TYPE_CODE", "").strip(),
                "ends":      list(ends_map.values()),
            })
    return runways


def load_airports() -> list[dict]:
    """Load and classify all public operational US airports.

    Result is cached in-process after the first call so batch workers that
    import this module in subprocess children each build once per process.

    Returns a list of dicts with keys:
      lat, lon, icao, iata, name, city, state, elev_ft,
      airport_type, max_rwy_ft, scheduled, runways

    `runways` is a list of {id, length_ft, width_ft, surface, ends: [...]}
    dicts (each end has {id, heading_true, lat, lon, elev_ft}); empty list
    if no runway geometry could be joined for that airport.
    """
    global _cache
    if _cache is not None:
        return _cache

    rwy_idx  = _build_rwy_index(_RWY_CSV)
    runways_by_apt = _build_runways(_RWY_CSV, _RWY_END_CSV)
    result: list[dict] = []

    with open(_BASE_CSV, encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            # Keep only public, operational, standard-land-airports
            if row.get("FACILITY_USE_CODE", "").strip() != "PU":
                continue
            if row.get("SITE_TYPE_CODE", "").strip() != "A":
                continue
            if row.get("ARPT_STATUS", "").strip() != "O":
                continue

            aid = row.get("ARPT_ID", "").strip()
            if not aid:
                continue

            try:
                lat = float(row["LAT_DECIMAL"])
                lon = float(row["LONG_DECIMAL"])
            except (ValueError, KeyError):
                continue

            max_rwy, has_paved = rwy_idx.get(aid, (0, False))
            if not has_paved or max_rwy < 1_000:
                continue

            far_139    = row.get("FAR_139_TYPE_CODE", "").strip()
            commercial = _int(row.get("COMMERCIAL_OPS", ""))
            commuter   = _int(row.get("COMMUTER_OPS", ""))
            based_jets = _int(row.get("BASED_JET_ENG", ""))
            jet_fuel   = _has_jet_fuel(row.get("FUEL_TYPES", "")) or based_jets > 0

            if far_139 and commercial > 0 and max_rwy >= 7_000:
                atype = "large_airport"
            elif (commuter > 0 or based_jets > 0) and jet_fuel and max_rwy >= 4_000:
                atype = "medium_airport"
            else:
                atype = "small_airport"

            icao = row.get("ICAO_ID", "").strip()

            result.append({
                "lat":          lat,
                "lon":          lon,
                "icao":         icao if icao else f"K{aid}",
                "iata":         aid,
                "name":         row.get("ARPT_NAME", "").strip(),
                "city":         row.get("CITY", "").strip(),
                "state":        row.get("STATE_CODE", "").strip(),
                "elev_ft":      _int(row.get("ELEV", ""), 0),
                "airport_type": atype,
                "max_rwy_ft":   max_rwy,
                "scheduled":    commercial > 0 or commuter > 0,
                "runways":      runways_by_apt.get(aid, []),
            })

    _cache = result
    return result
