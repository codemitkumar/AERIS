"""ICAO NOTAM reader — parses raw NOTAM text into structured, decoded data.

Understands the standard ICAO NOTAM format:

    A1234/26 NOTAMN
    Q) KZAB/QMRLC/IV/NBO/A/000/999/3546N10630W005
    A) KABQ
    B) 2607261200
    C) 2608261200 EST
    D) DAILY 1200-2359
    E) RWY 08/26 CLSD DUE WIP
    F) SFC
    G) UNL

Feed it raw NOTAM text (one or many) and get back `Notam` objects with the
Q-code decoded, timestamps parsed, coordinates/radius extracted, and the
E) free text expanded from contractions into plain English. No network I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Reference tables
# ---------------------------------------------------------------------------

# ICAO Doc 8126 Q-code subject/condition letters. Not exhaustive — covers the
# common cases; unknown codes fall back to the raw letters.
_Q_SUBJECT: dict[str, str] = {
    "AC": "approach control service", "AD": "aerodrome",
    "AE": "airspace reservation", "AF": "aerodrome/facility",
    "AH": "usable helicopter/heliport", "AL": "approach lighting system",
    "AN": "runway threshold lighting", "AO": "aeronautical information service",
    "AP": "airspace procedure", "AT": "control tower",
    "AU": "unmanned free balloon activity", "AV": "control area",
    "AW": "AWY", "AX": "intersection reporting point",
    "AZ": "aerodrome traffic zone",
    "CA": "air/ground communication facility", "CB": "ADS-B",
    "CC": "ADS-C", "CD": "CPDLC", "CE": "en-route surveillance radar",
    "CG": "ground controlled approach system", "CL": "selcal facility",
    "CM": "surface movement radar", "CP": "precision approach radar",
    "CR": "surveillance radar element of precision approach radar system",
    "CS": "secondary surveillance radar", "CT": "terminal area surveillance radar",
    "FA": "aerodrome (facility)", "FF": "fire fighting/rescue service",
    "GA": "GNSS airfield-specific",
    "IC": "ILS", "ID": "ILS DME", "IG": "glide path", "II": "inner marker",
    "IL": "localizer", "IM": "middle marker", "IN": "localizer (ILS/MLS)",
    "IO": "outer marker", "IS": "ILS category I", "IT": "ILS category II",
    "IU": "ILS category III", "IW": "MLS",
    "LA": "approach lighting", "LB": "aerodrome beacon",
    "LC": "runway centre line lights", "LD": "landing direction indicator lights",
    "LE": "runway edge lights", "LF": "sequenced flashing lights",
    "LG": "pilot-controlled lighting", "LH": "high intensity RWY lights",
    "LI": "runway end identifier lights", "LJ": "runway lead-in lighting system",
    "LK": "CAT II components of ALS", "LL": "low intensity RWY lights",
    "LM": "medium intensity RWY lights", "LP": "PAPI",
    "LR": "all landing area lighting facilities", "LS": "stopway lighting",
    "LT": "threshold lights", "LU": "helicopter approach path indicator",
    "LV": "VASIS", "LW": "heliport lighting", "LX": "taxiway centre line lights",
    "LY": "taxiway edge lights", "LZ": "runway touchdown zone lights",
    "MA": "movement area", "MB": "bearing strength", "MC": "clearway",
    "MD": "declared distances", "MG": "taxiing guidance system",
    "MH": "runway arresting gear", "MK": "parking area",
    "MM": "daylight markings", "MN": "apron", "MO": "stopbar",
    "MP": "aircraft stands", "MR": "runway",
    "MS": "runway strip/shoulder", "MT": "threshold", "MU": "runway turning bay",
    "MW": "strip/shoulder", "MX": "taxiway", "MY": "rapid exit taxiway",
    "NA": "ATC/ADS system", "NB": "ADS-B", "NC": "ADS-C", "NM": "multilateration",
    "OA": "aeronautical information service (AIP)", "OB": "obstacle",
    "OE": "aerodrome elevation", "OL": "obstacle lighting", "OR": "rescue coordination",
    "PA": "standard instrument arrival", "PB": "standard VFR arrival",
    "PC": "contingency procedure", "PD": "standard instrument departure",
    "PE": "standard VFR departure", "PF": "flow control procedure",
    "PH": "holding procedure", "PI": "instrument approach procedure",
    "PK": "VFR approach procedure", "PL": "flight plan processing",
    "PM": "aerodrome operating minima", "PN": "noise abatement procedure",
    "PO": "obstacle/aerodrome/instrument flight procedure",
    "PR": "special procedure", "PT": "transition altitude/level/procedure",
    "PU": "missed approach procedure", "PX": "minimum holding altitude",
    "PZ": "ADIZ",
    "RA": "radar service", "RD": "DME", "RM": "surveillance radar element",
    "RN": "NDB", "RO": "outer marker", "RP": "VOR", "RR": "monitoring system",
    "RS": "SSR", "RT": "terminal area surveillance radar",
    "RV": "PAR", "RW": "VOR/DME", "RX": "DF station",
    "SA": "automatic terminal information service", "SB": "ATC automatic system",
    "SC": "satellite communication network", "SE": "expected approach time",
    "SF": "flight information service", "SL": "logon address",
    "SM": "aeronautical mobile service", "SO": "SIGMET service",
    "SP": "public address system", "SS": "flight service station",
    "ST": "transmitter", "SV": "VOLMET service",
    "TA": "control area", "TC": "control area", "TF": "aerodrome flight information service",
    "TL": "transition level", "TT": "control area (terminal)", "TZ": "control zone",
    "WA": "air display", "WB": "aerobatics", "WC": "captive balloon/kite",
    "WD": "demolition of explosives", "WE": "exercise", "WF": "air refueling",
    "WG": "glider flying", "WH": "blasting", "WJ": "banner/target towing",
    "WL": "ascent of free balloon", "WM": "missile/gun/rocket firing",
    "WP": "aerial survey", "WR": "drone/UAS operations", "WS": "burning/blowing gas",
    "WT": "aerobatics/mass movement of aircraft", "WU": "unmanned aircraft/drone/UAS",
    "WV": "formation flight", "WW": "significant volcanic activity",
    "WY": "aerial survey/photography", "WZ": "model flying/kite flying",
    "XX": "other information not covered elsewhere",
}

_Q_CONDITION: dict[str, str] = {
    "AC": "withdrawn for maintenance", "AD": "available for daylight",
    "AF": "flight tested and found unreliable", "AG": "operating without auxiliary power",
    "AH": "hours of service changed", "AK": "resumed normal operations",
    "AL": "operative, subject to previously published limitations",
    "AM": "military operations only", "AN": "available for night",
    "AO": "operational",
    "AP": "prohibited", "AR": "available on request", "AS": "unserviceable",
    "AU": "not available", "AW": "completely withdrawn",
    "AX": "previously promulgated shutdown cancelled",
    "CA": "activated", "CC": "completed", "CD": "deactivated",
    "CE": "erected", "CF": "operating frequencies changed",
    "CG": "downgraded", "CH": "changed", "CI": "identification/radio call sign changed",
    "CL": "closed", "CM": "military operations only",
    "CN": "cancelled", "CO": "operating as normal", "CP": "operating civilian only",
    "CR": "temporarily replaced by", "CS": "installed", "CT": "on test, do not use",
    "HA": "expiring", "HN": "extended", "HD": "cancelled",
    "HE": "estimated", "HG": "downgraded", "HR": "temporarily replaced by",
    "LA": "operating normally", "LC": "closed", "LT": "limited to",
    "LH": "unserviceable", "LR": "unserviceable",
    "XX": "plain language",
}

_TRAFFIC = {"I": "IFR", "V": "VFR", "IV": "IFR and VFR", "K": "checklist"}
_PURPOSE = {
    "N": "immediate attention (NOTAMN)", "B": "operationally significant (PIB)",
    "O": "flight operations", "M": "miscellaneous", "K": "checklist",
}
_SCOPE = {
    "A": "aerodrome", "E": "en-route", "W": "navigation warning",
    "AE": "aerodrome and en-route", "AW": "aerodrome and warning",
    "K": "checklist",
}

# NOTAM contraction codes -> plain English, applied word-by-word.
_CONTRACTIONS: dict[str, str] = {
    "ACFT": "aircraft", "ACT": "active", "ADJ": "adjacent", "AGL": "above ground level",
    "AD": "aerodrome", "ALT": "altitude", "ALTN": "alternate", "AMDT": "amendment",
    "AMSL": "above mean sea level", "APCH": "approach", "APRX": "approximately",
    "APRON": "apron", "ARR": "arrival", "ASPH": "asphalt", "AVBL": "available",
    "AWY": "airway", "BCN": "beacon", "BTN": "between", "CHKD": "checked",
    "CLSD": "closed", "CLSG": "closing", "CNL": "cancelled", "CTC": "contact",
    "CTL": "control", "CTN": "caution", "DEP": "departure", "DLY": "daily",
    "DME": "distance measuring equipment", "DRG": "during", "DUE": "due to",
    "EFCT": "effect", "EXC": "except", "FLT": "flight", "FLW": "following",
    "FM": "from", "FREQ": "frequency", "GND": "ground", "HEL": "helicopter",
    "HR": "hour(s)", "HRS": "hours", "IAW": "in accordance with", "ILS": "instrument landing system",
    "IMM": "immediately", "INFO": "information", "INOP": "inoperative",
    "INTST": "intensity", "LGT": "lighting", "LGTD": "lighted", "LTD": "limited",
    "MAINT": "maintenance", "MIL": "military", "MNM": "minimum", "MNT": "monitor",
    "NAV": "navigation", "NM": "nautical mile(s)", "OBST": "obstacle",
    "OPN": "operation", "OPR": "operate/operative/operator", "OTS": "out of service",
    "PCL": "pilot controlled lighting", "PRKG": "parking", "PROC": "procedure",
    "PSN": "position", "PPR": "prior permission required", "RTE": "route",
    "RWY": "runway", "SFC": "surface", "SKED": "scheduled", "TFC": "traffic",
    "TIL": "until", "TWR": "tower", "TWY": "taxiway", "U/S": "unserviceable",
    "UNL": "unlimited", "UAS": "unmanned aircraft system", "UAV": "unmanned aerial vehicle",
    "VIS": "visibility", "VOR": "VHF omnidirectional range", "WEF": "with effect from",
    "WI": "within", "WIE": "with immediate effect", "WIP": "work in progress",
    "WKG": "working", "WX": "weather", "TEMPO": "temporary", "PERM": "permanent",
    "EST": "estimated", "NR": "number", "MAX": "maximum", "MIN": "minimum",
    "TWYS": "taxiways", "RWYS": "runways", "SVC": "service", "STN": "station",
    "SN": "snow", "ICE": "ice", "DGR": "danger", "EQPT": "equipment",
    "ELEV": "elevation", "EXTD": "extended", "FLG": "flashing", "H24": "continuous, 24 hours",
}

_COORD_RE = re.compile(r"(\d{2,4})([NS])(\d{3,5})([EW])(\d{3})")
_TIME_RE = re.compile(r"^(\d{10})(?:\s*(EST))?$")


@dataclass
class Notam:
    raw: str
    number: str | None = None
    year: str | None = None
    notam_type: str = "NEW"          # NEW / REPLACE / CANCEL
    replaces: str | None = None       # target of NOTAMR / NOTAMC

    fir: str | None = None
    q_code: str | None = None
    subject: str | None = None
    condition: str | None = None
    traffic: str | None = None
    purpose: str | None = None
    scope: str | None = None
    lower_limit_ft: int | None = None
    upper_limit_ft: int | None = None
    center: tuple[float, float] | None = None
    radius_nm: int | None = None

    locations: list[str] = field(default_factory=list)
    effective_start: datetime | None = None
    effective_end: datetime | None = None
    permanent: bool = False
    estimated_end: bool = False
    schedule: str | None = None
    lower_limit: str | None = None
    upper_limit: str | None = None
    body: str = ""

    @property
    def subject_desc(self) -> str:
        return _Q_SUBJECT.get(self.subject, self.subject or "unknown subject")

    @property
    def condition_desc(self) -> str:
        return _Q_CONDITION.get(self.condition, self.condition or "unknown condition")

    @property
    def decoded_body(self) -> str:
        return expand_contractions(self.body)

    @property
    def summary(self) -> str:
        loc = "/".join(self.locations) or self.fir or "?"
        when = self.effective_start.strftime("%Y-%m-%d %H:%MZ") if self.effective_start else "?"
        return f"[{self.number or '?'}] {loc}: {self.subject_desc} — {self.condition_desc} (from {when})"

    def to_dict(self) -> dict:
        d = {
            k: v for k, v in self.__dict__.items()
        }
        d["effective_start"] = self.effective_start.isoformat() if self.effective_start else None
        d["effective_end"] = self.effective_end.isoformat() if self.effective_end else None
        d["subject_desc"] = self.subject_desc
        d["condition_desc"] = self.condition_desc
        d["decoded_body"] = self.decoded_body
        d["summary"] = self.summary
        return d


def expand_contractions(text: str) -> str:
    """Expand NOTAM contraction codes in `text` into plain English words."""
    if not text:
        return text

    def repl(match: re.Match) -> str:
        token = match.group(0)
        plain = _CONTRACTIONS.get(token.upper())
        return plain if plain else token

    # Match U/S specially (contains a slash) before generic word tokens.
    text = re.sub(r"\bU/S\b", _CONTRACTIONS["U/S"], text, flags=re.IGNORECASE)
    return re.sub(r"[A-Z0-9]+", repl, text)


def _parse_coord_radius(token: str) -> tuple[tuple[float, float] | None, int | None]:
    """Parse the trailing `DDMMN/DDDMME + radius` field of a Q) line."""
    m = _COORD_RE.match(token)
    if not m:
        return None, None
    lat_raw, lat_hem, lon_raw, lon_hem, radius = m.groups()

    def _dms_to_dd(raw: str) -> float:
        # DDMM (lat, 4 digits) or DDDMM (lon, 5 digits) -> decimal degrees.
        minutes = int(raw[-2:])
        degrees = int(raw[:-2])
        return round(degrees + minutes / 60.0, 4)

    lat = _dms_to_dd(lat_raw) * (1 if lat_hem == "N" else -1)
    lon = _dms_to_dd(lon_raw) * (1 if lon_hem == "E" else -1)
    return (lat, lon), int(radius)


def _parse_time(token: str) -> tuple[datetime | None, bool]:
    """Parse a B)/C) field: 'YYMMDDHHMM', optionally with a trailing EST."""
    token = token.strip()
    if token.upper() in ("PERM", "PERMANENT"):
        return None, False
    if token.upper() in ("UFN",):  # until further notice
        return None, False

    m = _TIME_RE.match(token)
    if not m:
        return None, False
    digits, est = m.groups()
    yy, mm, dd, hh, mi = digits[0:2], digits[2:4], digits[4:6], digits[6:8], digits[8:10]
    year = 2000 + int(yy)
    try:
        dt = datetime(year, int(mm), int(dd), int(hh), int(mi), tzinfo=timezone.utc)
    except ValueError:
        return None, bool(est)
    return dt, bool(est)


_FIELD_RE = re.compile(
    r"(?ms)^([A-GQ])\)\s*(.*?)(?=^[A-GQ]\)\s|\Z)"
)
_HEADER_RE = re.compile(
    r"([A-Z]?\d{2,5}/\d{2,4})\s+(NOTAM[NRC])(?:\s+([A-Z]?\d{2,5}/\d{2,4}))?"
)


def parse_notam(text: str) -> Notam:
    """Parse a single raw ICAO-format NOTAM into a `Notam`."""
    raw = text.strip()
    notam = Notam(raw=raw)

    header = _HEADER_RE.search(raw)
    if header:
        notam.number = header.group(1)
        notam.year = header.group(1).split("/")[-1]
        kind = header.group(2)
        notam.notam_type = {"NOTAMN": "NEW", "NOTAMR": "REPLACE", "NOTAMC": "CANCEL"}[kind]
        if header.group(3):
            notam.replaces = header.group(3)

    fields: dict[str, str] = {}
    for m in _FIELD_RE.finditer(raw):
        letter, content = m.group(1), m.group(2).strip()
        content = re.sub(r"\s+", " ", content)
        fields[letter] = content

    if "Q" in fields:
        parts = fields["Q"].split("/")
        if len(parts) >= 8:
            notam.fir = parts[0].strip()
            notam.q_code = parts[1].strip()
            if notam.q_code.startswith("Q") and len(notam.q_code) == 5:
                notam.subject = notam.q_code[1:3]
                notam.condition = notam.q_code[3:5]
            notam.traffic = parts[2].strip()
            notam.purpose = parts[3].strip()
            notam.scope = parts[4].strip()
            notam.lower_limit_ft = _int_or_none(parts[5])
            notam.upper_limit_ft = _int_or_none(parts[6])
            notam.center, notam.radius_nm = _parse_coord_radius(parts[7].strip())

    if "A" in fields:
        notam.locations = [loc.strip() for loc in fields["A"].split(",") if loc.strip()]

    if "B" in fields:
        notam.effective_start, _ = _parse_time(fields["B"])

    if "C" in fields:
        if fields["C"].strip().upper() in ("PERM", "PERMANENT"):
            notam.permanent = True
        else:
            notam.effective_end, notam.estimated_end = _parse_time(fields["C"])

    if "D" in fields:
        notam.schedule = fields["D"]

    if "E" in fields:
        notam.body = fields["E"]

    if "F" in fields:
        notam.lower_limit = fields["F"]

    if "G" in fields:
        notam.upper_limit = fields["G"]

    return notam


def parse_notams(text: str) -> list[Notam]:
    """Parse a block of raw text containing one or more NOTAMs.

    NOTAMs are split on blank-line boundaries preceding a NOTAM number
    (e.g. 'A1234/26 NOTAMN'), so a plain-text NOTAM bulletin/PIB can be fed
    in directly.
    """
    chunks = re.split(r"\n(?=[A-Z]?\d{2,5}/\d{2,4}\s+NOTAM[NRC])", text.strip())
    return [parse_notam(chunk) for chunk in chunks if chunk.strip()]


def _int_or_none(token: str) -> int | None:
    token = token.strip()
    if not token or not token.isdigit():
        return None
    return int(token)


if __name__ == "__main__":
    sample = """
A1234/26 NOTAMN
Q) KZAB/QMRLC/IV/NBO/A/000/999/3546N10630W005
A) KABQ
B) 2607261200
C) 2608261200 EST
D) DAILY 1200-2359
E) RWY 08/26 CLSD DUE WIP
F) SFC
G) UNL

A5678/26 NOTAMN
Q) KZDV/QOBCE/IV/M/AE/000/199/3939N10459W010
A) KDEN
B) 2607200600
C) PERM
E) OBST TOWER 199FT AGL LGTD ERECTED 5NM N AD
"""
    for n in parse_notams(sample):
        print(n.summary)
        print("  ", n.decoded_body)
