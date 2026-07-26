"""Export airport + runway geometry for the /simulateMap UI.

load_airports() already joins runway geometry (per-end lat/lon/elevation +
true heading) onto each airport dict — this script just writes that data
out as a static JSON consumed by AERIS_UI at runtime.

Usage:
    python -m data.ingestion.export_map_data
"""

import json
from pathlib import Path

from data.ingestion.faa_airport_loader import load_airports

_OUT_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "AERIS_UI" / "public" / "data" / "airports.json"
)


def main():
    data = load_airports()
    _OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))

    n_rwy = sum(len(a["runways"]) for a in data)
    print(f"[export_map_data] {len(data)} airports, {n_rwy} runways -> {_OUT_PATH}")


if __name__ == "__main__":
    main()
