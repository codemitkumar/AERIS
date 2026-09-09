"""European airport set derived from EUROCONTROL flight-plan data.

Builds an airport list in the same dict shape as
data.ingestion.faa_airport_loader.load_airports(), from the ADEP/ADES
fields of real flight plans (see eurocontrol_loader.load_flights) rather
than from an airport/runway database — EUROCONTROL's R&D release has no
runway geometry.

IMPORTANT — GRACE eligibility: every airport here has runways: [] and
max_rwy_ft: 0 (runway_verified: False), because no real runway data backs
it. decision.diversion_selector's hard runway-floor constraint is never
relaxed, so these airports will always fail GRACE's feasibility check and
are effectively excluded as diversion candidates. This dataset is for
scenario realism (origin/destination selection, alternates for
non-GRACE-scored purposes) and map/route rendering — not for extending
GRACE's candidate pool. Pairing with a real runway source (e.g.
OurAirports) is future work, not done here to avoid fabricating runway
lengths.

`airport_type` (large/medium/small) is inferred from how often an ICAO
appears as ADEP or ADES in the sample month — a traffic-volume proxy, not
a certified classification like the FAA loader's FAR-139/runway rules.
"""

from collections import Counter

from data.ingestion.eurocontrol_loader import DEFAULT_MONTH, load_flights

_cache: dict[str, list[dict]] = {}

# Traffic-count thresholds (flights as ADEP+ADES combined, per sample month)
# for the large/medium/small proxy classification.
_LARGE_THRESHOLD = 500
_MEDIUM_THRESHOLD = 50


def load_airports(month: str = DEFAULT_MONTH) -> list[dict]:
    """Derive a European airport list from real flight-plan ADEP/ADES data.

    Cached in-process per month. Returns dicts with the same keys as
    faa_airport_loader.load_airports() (lat, lon, icao, iata, name,
    airport_type, max_rwy_ft, scheduled, runways) plus runway_verified
    (always False here — see module docstring).
    """
    if month in _cache:
        return _cache[month]

    flights = load_flights(month)

    positions: dict[str, tuple[float, float]] = {}
    counts: Counter = Counter()

    for fl in flights:
        if fl["adep"] and fl["adep"] not in positions:
            positions[fl["adep"]] = (fl["adep_lat"], fl["adep_lon"])
        if fl["ades"] and fl["ades"] not in positions:
            positions[fl["ades"]] = (fl["ades_lat"], fl["ades_lon"])
        counts[fl["adep"]] += 1
        counts[fl["ades"]] += 1

    result: list[dict] = []
    for icao, (lat, lon) in positions.items():
        n = counts[icao]
        if n >= _LARGE_THRESHOLD:
            atype = "large_airport"
        elif n >= _MEDIUM_THRESHOLD:
            atype = "medium_airport"
        else:
            atype = "small_airport"

        result.append({
            "lat":             lat,
            "lon":             lon,
            "icao":            icao,
            "iata":            "",
            "name":            icao,
            "city":            "",
            "state":           "",
            "elev_ft":         0,
            "airport_type":    atype,
            "max_rwy_ft":      0,
            "scheduled":       n >= _MEDIUM_THRESHOLD,
            "runways":         [],
            "runway_verified": False,
        })

    _cache[month] = result
    return result
