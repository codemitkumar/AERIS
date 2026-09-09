"""Seven scenarios exercising the mechanisms compared in the paper's
evaluation. Every position is a real point in/near CONUS airspace and every
airport referenced is a real FAA NASR entry; scenarios were constructed by
searching real aircraft-state/geometry combinations that exercise a specific
mechanism (e.g. minimum turn to any reachable candidate exceeding a turn
budget), not hand-picked to force a particular algorithm's answer -- results
are reported as found, including cases where a hoped-for differentiation
does not clearly appear.

Each scenario also carries a `reference` field describing the
author-defined, domain-reasoned expected behavior used for the top-1
agreement metric. Where no single airport is uniquely correct (e.g. normal
cruise), `reference` is a criterion description rather than one ICAO code,
and that row is excluded from the top-1-agreement statistic (noted in the
paper).
"""

SCENARIOS = {
    "S1_normal_cruise": {
        "description": "Nominal cruise, no active conditions, LOW severity.",
        "state": {
            "lat": 38.0, "lon": -98.0, "heading_deg": 90.0, "track_deg": 90.0,
            "groundspeed_kts": 460.0,
            "fuel_total_lbs": 20000.0, "fuel_flow_total_lbs_hr": 5000.0,
            "fuel_reserve_min_lbs": 3000.0, "time": 3600.0,
        },
        "severity": "LOW",
        "active_conditions": [],
        "reference": "no single correct answer; used only for inter-algorithm agreement",
    },
    "S2_engine_failure": {
        "description": "ENGINE_FAILURE active (prefer larger/medium airport); "
                        "nearest candidate is a small strip, a large airport is "
                        "reachable only slightly farther.",
        "state": {
            "lat": 38.15, "lon": -90.62, "heading_deg": 316.4, "track_deg": 316.4,
            "groundspeed_kts": 250.0,
            "fuel_total_lbs": 15000.0, "fuel_flow_total_lbs_hr": 5000.0,
            "fuel_reserve_min_lbs": 3000.0, "time": 3600.0,
        },
        "severity": "HIGH",
        "active_conditions": ["ENGINE_FAILURE"],
        "reference": "large_airport or medium_airport preferred over the nearer small_airport",
    },
    "S3_fuel_leak": {
        "description": "FUEL_LEAK active with fuel already close to reserve; "
                        "worst-case (2x) burn should sharply cut the reachable set.",
        "state": {
            "lat": 38.0, "lon": -98.0, "heading_deg": 90.0, "track_deg": 90.0,
            "groundspeed_kts": 250.0,
            "fuel_total_lbs": 1600.0, "fuel_flow_total_lbs_hr": 4500.0,
            "fuel_reserve_min_lbs": 1200.0, "time": 3600.0,
        },
        "severity": "HIGH",
        "active_conditions": ["FUEL_LEAK"],
        "reference": "no candidate with negative worst-case fuel margin",
    },
    "S4_go_around": {
        "description": "GO_AROUND active near KICT; destination should be "
                        "strongly preferred despite a nontrivial missed-approach turn.",
        "state": {
            "lat": 37.6999525, "lon": -97.45304277, "heading_deg": 270.0, "track_deg": 270.0,
            "groundspeed_kts": 160.0,
            "fuel_total_lbs": 8000.0, "fuel_flow_total_lbs_hr": 4500.0,
            "fuel_reserve_min_lbs": 2500.0, "time": 5400.0,
            "origin_icao": "KHUT", "destination_icao": "KICT",
        },
        "severity": "MODERATE",
        "active_conditions": ["GO_AROUND"],
        "reference": "KICT",
    },
    "S5_early_departure": {
        "description": "Emergency 3 minutes after KHUT departure; origin is "
                        "nearly directly behind the aircraft.",
        "state": {
            "lat": 38.10, "lon": -97.80, "heading_deg": 90.0, "track_deg": 90.0,
            "groundspeed_kts": 250.0,
            "fuel_total_lbs": 15000.0, "fuel_flow_total_lbs_hr": 4500.0,
            "fuel_reserve_min_lbs": 2500.0, "time": 180.0,
            "origin_icao": "KHUT", "destination_icao": "KICT",
        },
        "severity": "HIGH",
        "active_conditions": [],
        "reference": "KHUT",
    },
    "S6_severe_turn": {
        "description": "DIRECTIONAL_CONTROL_LOSS active (45 deg turn budget); "
                        "every candidate within realistic range requires >45 deg "
                        "at full strictness, forcing relaxation.",
        "state": {
            "lat": 34.71392141080014, "lon": -101.75393624455805,
            "heading_deg": 122.41931480364363, "track_deg": 122.4,
            "groundspeed_kts": 250.0,
            "fuel_total_lbs": 15000.0, "fuel_flow_total_lbs_hr": 4500.0,
            "fuel_reserve_min_lbs": 2500.0, "time": 3600.0,
        },
        "severity": "HIGH",
        "active_conditions": ["DIRECTIONAL_CONTROL_LOSS"],
        "reference": "any candidate satisfying the relaxed turn budget (not empty)",
    },
    "S7_no_reachable": {
        "description": "Mid-Atlantic position (far outside the FAA NASR dataset "
                        "footprint), fuel effectively exhausted.",
        "state": {
            "lat": 35.0, "lon": -40.0, "heading_deg": 90.0, "track_deg": 90.0,
            "groundspeed_kts": 460.0,
            "fuel_total_lbs": 5.0, "fuel_flow_total_lbs_hr": 5000.0,
            "fuel_reserve_min_lbs": 3000.0, "time": 3600.0,
        },
        "severity": "HIGH",
        "active_conditions": [],
        "reference": "None (no feasible candidate exists)",
    },
}

DEFAULT_IS_TRANSPORT = True
DEFAULT_VREF_KTS = 140.0
