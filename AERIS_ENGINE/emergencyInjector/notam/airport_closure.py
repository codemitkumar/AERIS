"""Randomly closes runways / taxiways / whole airports for a simulation.

Unlike the other injectors in emergencyInjector/, this doesn't degrade an
aircraft system tick by tick — it seeds the flight with realistic NOTAM-style
airport closures, the kind of thing a real crew would see in a pre-flight
NOTAM briefing. Closures land on a mix of:

  - "on path" airports — the destination, or airports that sit within a
    corridor around the direct origin→destination great-circle route
    (i.e. plausible enroute diversion candidates), and
  - "scattered" airports — anywhere else in the loaded airport dataset,
    exactly like real NOTAMs that have nothing to do with this particular
    flight but are active in the system regardless.

Each closure is built as a `data.ingestion.notam_reader.Notam`, so the exact
same reader that decodes real-world NOTAM text can decode these too.

Terminal commands
------------------
    inject notam [count]
    clear notam
"""

import math
import random
from datetime import datetime, timedelta, timezone

from data.ingestion.FlightGenerator import bearing, haversine_nm
from data.ingestion.notam_reader import Notam

_EARTH_R_NM = 3440.065

_REASONS = [
    "WIP", "MAINT", "SNOW REMOVAL", "CONSTRUCTION", "RWY INSPECTION",
    "PAVEMENT REPAIR", "DISABLED ACFT", "WILDLIFE HAZARD", "CRANE OPS",
    "LGT MAINT", "EQPT OUTAGE", "MARKING",
]

_SERIES_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"   # ICAO NOTAM series letters (no I/O)
_FALLBACK_RWY_IDS = ["09/27", "04/22", "18/36", "13/31", "07/25", "16/34"]
_TWY_IDS = ["A", "B", "C", "D", "E", "F", "G", "K"]


def _clamp1(x: float) -> float:
    return max(-1.0, min(1.0, x))


def _format_raw(n: Notam) -> str:
    """Render a Notam back into ICAO-format raw text (log/display symmetry
    with notam_reader.parse_notam, which can parse this straight back)."""
    coord = ""
    if n.center:
        lat, lon = n.center
        lat_dm = abs(lat)
        lon_dm = abs(lon)
        lat_str = f"{int(lat_dm):02d}{round((lat_dm % 1) * 60):02d}{'N' if lat >= 0 else 'S'}"
        lon_str = f"{int(lon_dm):03d}{round((lon_dm % 1) * 60):02d}{'E' if lon >= 0 else 'W'}"
        coord = f"{lat_str}{lon_str}{n.radius_nm:03d}"

    c_field = "PERM" if n.permanent else n.effective_end.strftime("%y%m%d%H%M")
    if not n.permanent and n.estimated_end:
        c_field += " EST"

    return "\n".join([
        f"{n.number} NOTAMN",
        f"Q) {n.fir}/Q{n.subject}{n.condition}/{n.traffic}/{n.purpose}/{n.scope}/"
        f"{n.lower_limit_ft:03d}/{n.upper_limit_ft:03d}/{coord}",
        f"A) {','.join(n.locations)}",
        f"B) {n.effective_start.strftime('%y%m%d%H%M')}",
        f"C) {c_field}",
        f"E) {n.body}",
        f"F) {n.lower_limit}",
        f"G) {n.upper_limit}",
    ])


class NotamInjector:
    """Seeds the simulation with airport/runway/taxiway closure NOTAMs.

    Closures are generated once — at the start of the flight, since real
    NOTAMs are briefed pre-departure — then republished into `state` every
    tick so any downstream consumer always sees the current closure list.
    """

    NAME = "NOTAM Closures"

    def __init__(self):
        self.active = False
        self.closures: list[dict] = []              # [{icao, on_path, notam}, ...]
        self.closed_airport_icaos: set[str] = set()
        self.closed_runways: dict[str, set[str]] = {}

    # ── control ──────────────────────────────────────────────────────────

    def start(self, gen, count: int | None = None, corridor_nm: float = 60.0) -> None:
        """Pick `count` airports (mixing on-path and scattered) and close them.

        Defaults to 10 closures when `count` isn't given.
        """
        self.closures = []
        self.closed_airport_icaos = set()
        self.closed_runways = {}

        airports = gen.airports
        if not airports:
            self.active = False
            return

        count = max(1, count if count is not None else 10)

        on_path_pool, scattered_pool = self._split_pools(gen, airports, corridor_nm)

        on_path_n = min(random.randint(1, max(1, count - 1)), len(on_path_pool))
        scattered_n = min(count - on_path_n, len(scattered_pool))

        chosen: list[tuple[dict, bool]] = (
            [(a, True) for a in random.sample(on_path_pool, on_path_n)] +
            [(a, False) for a in random.sample(scattered_pool, scattered_n)]
        )

        # Sparse route/airport pool may fall short of `count` — top up from
        # whichever scattered candidates are still unused.
        deficit = count - len(chosen)
        if deficit > 0:
            chosen_icaos = {a["icao"] for a, _ in chosen}
            leftover = [a for a in scattered_pool if a["icao"] not in chosen_icaos]
            random.shuffle(leftover)
            chosen += [(a, False) for a in leftover[:deficit]]

        now = datetime.now(timezone.utc)
        for apt, on_path in chosen:
            notam, closed_rwy_ids = self._build_closure_notam(apt, now)
            self._apply_closure(apt, notam, closed_rwy_ids)
            self.closures.append({
                "icao":              apt["icao"],
                "on_path":           on_path,
                "notam":             notam,
                "closed_runway_ids": closed_rwy_ids,
            })

        self.active = bool(self.closures)
        if self.active:
            tags = ", ".join(
                f"{c['icao']}{'*' if c['on_path'] else ''}" for c in self.closures
            )
            print(f"[INJECT] notam  {len(self.closures)} closure(s)  {tags}  (* = on flight path)")

    def stop(self) -> None:
        self.active = False
        self.closures = []
        self.closed_airport_icaos = set()
        self.closed_runways = {}
        print("[INJECT] notam cleared")

    # ── per-tick publish ─────────────────────────────────────────────────

    def __call__(self, state: dict, gen) -> None:
        if not self.active:
            return
        state["active_notams"] = [c["notam"].summary for c in self.closures]
        state["notam_closed_airports"] = sorted(self.closed_airport_icaos)
        state["notam_closed_runways"] = {
            icao: sorted(rwy_ids) for icao, rwy_ids in self.closed_runways.items()
        }
        state["notam_closures"] = [
            {
                "icao":            c["icao"],
                "onPath":          c["on_path"],
                "kind":            c["notam"].subject,       # AD / MR / MX / MN
                "kindDesc":        c["notam"].subject_desc,  # "aerodrome" / "runway" / ...
                "closedRunwayIds": c["closed_runway_ids"],
                "summary":         c["notam"].summary,
            }
            for c in self.closures
        ]
        state["destination_notam_closed"] = gen.dest["icao"] in self.closed_airport_icaos
        state["alternates_notam_closed"] = [
            a["icao"] for a in gen.alternates if a["icao"] in self.closed_airport_icaos
        ]

    # ── queries ──────────────────────────────────────────────────────────

    def is_airport_closed(self, icao: str) -> bool:
        return icao in self.closed_airport_icaos

    def is_runway_closed(self, icao: str, rwy_id: str) -> bool:
        return rwy_id in self.closed_runways.get(icao, set())

    # ── airport selection ───────────────────────────────────────────────

    def _split_pools(self, gen, airports: list[dict], corridor_nm: float):
        """Partition the airport dataset into route-corridor vs everything else."""
        o_lat, o_lon = gen.origin["lat"], gen.origin["lon"]
        d_lat, d_lon = gen.dest["lat"], gen.dest["lon"]
        route_dist = gen.route_distance_nm
        route_brg = bearing(o_lat, o_lon, d_lat, d_lon)

        exclude = {gen.origin["icao"]}
        on_path, scattered = [], []

        for apt in airports:
            icao = apt.get("icao")
            if not icao or icao in exclude:
                continue

            d13 = haversine_nm(o_lat, o_lon, apt["lat"], apt["lon"])
            if d13 < 0.5:
                continue  # essentially the origin itself

            brg13 = bearing(o_lat, o_lon, apt["lat"], apt["lon"])
            cross_nm = math.asin(
                _clamp1(math.sin(d13 / _EARTH_R_NM) * math.sin(math.radians(brg13 - route_brg)))
            ) * _EARTH_R_NM
            along_nm = math.acos(
                _clamp1(math.cos(d13 / _EARTH_R_NM) / math.cos(cross_nm / _EARTH_R_NM))
            ) * _EARTH_R_NM

            if abs(cross_nm) <= corridor_nm and -corridor_nm <= along_nm <= route_dist + corridor_nm:
                on_path.append(apt)
            else:
                scattered.append(apt)

        return on_path, scattered

    # ── NOTAM generation ─────────────────────────────────────────────────

    def _build_closure_notam(self, apt: dict, now: datetime) -> tuple[Notam, list[str]]:
        kind = random.choices(
            ["airport", "runway", "taxiway", "apron"], weights=[15, 55, 20, 10],
        )[0]

        runways = apt.get("runways") or []
        rwy_ids = [r["id"] for r in runways if r.get("id")]
        reason = random.choice(_REASONS)
        closed_rwy_ids: list[str] = []

        if kind == "airport":
            subject, condition = "AD", "CL"
            body = f"AD CLSD DUE {reason}"
        elif kind == "runway":
            if rwy_ids:
                n = min(len(rwy_ids), 1 if random.random() < 0.7 else 2)
                closed_rwy_ids = random.sample(rwy_ids, n)
            else:
                closed_rwy_ids = [random.choice(_FALLBACK_RWY_IDS)]
            subject, condition = "MR", "LC"
            body = f"RWY {' AND RWY '.join(closed_rwy_ids)} CLSD DUE {reason}"
        elif kind == "taxiway":
            subject, condition = "MX", "LC"
            body = f"TWY {random.choice(_TWY_IDS)} CLSD DUE {reason}"
        else:  # apron
            subject, condition = "MN", "LC"
            body = f"APRON CLSD DUE {reason}"

        permanent = random.random() < 0.12
        start = now - timedelta(hours=random.uniform(0, 48))
        end = None
        estimated_end = False
        if not permanent:
            duration_h = random.choice([
                random.uniform(2, 12),
                random.uniform(12, 72),
                random.uniform(72, 24 * 14),
            ])
            end = start + timedelta(hours=duration_h)
            estimated_end = random.random() < 0.2

        number = f"{random.choice(_SERIES_LETTERS)}{random.randint(1, 9999):04d}/{now.strftime('%y')}"

        notam = Notam(
            raw="",
            number=number,
            year=now.strftime("%y"),
            notam_type="NEW",
            fir=apt["icao"],
            q_code=f"Q{subject}{condition}",
            subject=subject,
            condition=condition,
            traffic="IV",
            purpose="NBO",
            scope="A",
            lower_limit_ft=0,
            upper_limit_ft=999,
            center=(round(apt["lat"], 4), round(apt["lon"], 4)),
            radius_nm=5,
            locations=[apt["icao"]],
            effective_start=start,
            effective_end=end,
            permanent=permanent,
            estimated_end=estimated_end,
            lower_limit="SFC",
            upper_limit="UNL",
            body=body,
        )
        notam.raw = _format_raw(notam)
        return notam, closed_rwy_ids

    def _apply_closure(self, apt: dict, notam: Notam, closed_rwy_ids: list[str]) -> None:
        icao = apt["icao"]
        if notam.subject == "AD":
            self.closed_airport_icaos.add(icao)
        elif notam.subject == "MR" and closed_rwy_ids:
            self.closed_runways.setdefault(icao, set()).update(closed_rwy_ids)
