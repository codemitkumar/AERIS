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

_UTILS_DIR = Path(__file__).resolve().parent.parent.parent / "utils"
_BASE_CSV  = _UTILS_DIR / "APT_BASE.csv"
_RWY_CSV   = _UTILS_DIR / "APT_RWY.csv"

_cache: list[dict] | None = None


def _int(val: str, default: int = 0) -> int:
    try:
        return int(str(val).strip()) if val and str(val).strip() else default
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


def load_airports() -> list[dict]:
    """Load and classify all public operational US airports.

    Result is cached in-process after the first call so batch workers that
    import this module in subprocess children each build once per process.

    Returns a list of dicts with keys:
      lat, lon, icao, iata, name, city, state, elev_ft,
      airport_type, max_rwy_ft, scheduled
    """
    global _cache
    if _cache is not None:
        return _cache

    rwy_idx = _build_rwy_index(_RWY_CSV)
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
            })

    _cache = result
    return result
