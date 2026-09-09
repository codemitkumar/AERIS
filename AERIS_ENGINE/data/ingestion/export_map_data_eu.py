"""Export EUROCONTROL-derived map data (European airports, FIR/UIR
boundary polygons, ATS route network) for the /simulateMap UI's EU region.

Counterpart to export_map_data.py (FAA/US). Writes three static JSON files
consumed by AERIS_UI at runtime.

Usage:
    python -m data.ingestion.export_map_data_eu
"""

import json
from pathlib import Path

from data.ingestion.eurocontrol_airport_loader import load_airports
from data.ingestion.eurocontrol_loader import DEFAULT_MONTH, load_fir_polygons, load_routes

_OUT_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "AERIS_UI" / "public" / "data"
)


def main():
    _OUT_DIR.mkdir(parents=True, exist_ok=True)

    airports = load_airports()
    with open(_OUT_DIR / "eu_airports.json", "w", encoding="utf-8") as f:
        json.dump(airports, f, separators=(",", ":"))
    print(f"[export_map_data_eu] {len(airports)} airports -> eu_airports.json")

    polygons = load_fir_polygons()
    fir_out = {
        aid: {"min_fl": p["min_fl"], "max_fl": p["max_fl"], "points": p["points"]}
        for aid, p in polygons.items()
    }
    with open(_OUT_DIR / "fir_polygons.json", "w", encoding="utf-8") as f:
        json.dump(fir_out, f, separators=(",", ":"))
    print(f"[export_map_data_eu] {len(fir_out)} FIR/UIR polygons -> fir_polygons.json")

    routes = load_routes()
    with open(_OUT_DIR / "routes.json", "w", encoding="utf-8") as f:
        json.dump(routes, f, separators=(",", ":"))
    print(f"[export_map_data_eu] {len(routes)} routes -> routes.json")

    print(f"[export_map_data_eu] source month: {DEFAULT_MONTH}")


if __name__ == "__main__":
    main()
