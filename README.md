# AERIS, Aviation Emergency Response Intelligence System

## Abstract

AERIS is an AI-assisted cockpit decision-support framework designed to reduce pilot workload during abnormal and emergency flight situations. The system does not seek to replace the pilot-in-command; rather, it functions as an intelligent advisory layer that continuously monitors aircraft state, assesses the severity of identified hazards, and generates structured recommendations calibrated to the crew's current operational context. The framework is of particular value in high-workload scenarios involving multiple simultaneous failures, where existing procedural checklists are often sequential and may not adequately address the combined systemic effects of concurrent faults.

The architecture is organised into four successive layers: deterministic real-time monitoring, subsystem health aggregation, operational airport reasoning, and AI-driven decision support. Layers progress from physically grounded mathematical analysis at the sensor level toward contextually aware, explainable AI recommendations at the crew-interface level.

---

## 1. Research Motivation

Modern commercial aircraft have achieved extraordinary levels of mechanical reliability. However, incident and accident data consistently identify crew decision-making under high workload, particularly in non-normal situations, as a primary causal or contributing factor. The problem is not a lack of information but an excess of it: when multiple systems degrade simultaneously, the crew must simultaneously diagnose failures, consult non-linear checklist sequences, assess diversion feasibility, and manage the aircraft, all against an acute time constraint.

Existing avionics advisories (ECAM, EICAS, GPWS) are reactive and singular, they detect and annunciate individual events in isolation. They do not aggregate across systems, they do not assess the combined operability of the aircraft, and they do not reason about whether the available diversion airports are actually reachable and suitable given the aircraft's degraded state.

AERIS addresses this gap by introducing an intelligent intermediary between raw sensor data and the crew, capable of holistic situational assessment and structured recommendation generation.

---

## 2. System Architecture

AERIS is structured as a four-layer pipeline. Each layer builds on the outputs of the layer beneath it, progressively abstracting raw sensor data toward actionable crew advisories.

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 4 - AI Reasoning & Decision Support                  │
│  Multi-failure prioritisation · Learned severity classification │
│  Adaptive checklist generation                              │
├─────────────────────────────────────────────────────────────┤
│  Layer 3 - Operational Airport Knowledge Base (GRACE)        │
│  Runway suitability · Reachability · Turn feasibility        │
│  Severity-weighted multi-factor diversion ranking            │
├─────────────────────────────────────────────────────────────┤
│  Layer 2 - Subsystem Health Aggregation                     │
│  Fault fusion · Explainable aircraft health model           │
│  Per-subsystem confidence scores                            │
├─────────────────────────────────────────────────────────────┤
│  Layer 1 - Deterministic Mathematical Monitoring            │
│  53 independent real-time analytical modules                │
│  30 Hz event-driven architecture · Physics-grounded alerts  │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. Layer 1, Deterministic Mathematical Monitoring Engine

### 3.1 Overview

The monitoring engine (`AERIS_ENGINE`) constitutes the foundational layer of the framework. It implements 53 independent physics-grounded monitoring modules that evaluate aircraft state in real time to detect conditions across ten operational domains. Each module is analytically deterministic: its alerting logic is derived from published regulatory limits, aircraft flight manual references, and ICAO standard procedures, without the use of learned models. This ensures that the outputs of Layer 1 are fully explainable and directly traceable to their causal sensor inputs, a prerequisite for the explainability requirements of Layers 2 and 4.

### 3.2 Event-Driven Architecture

The engine operates as a 30 Hz discrete-time state machine. At each tick, a flat state dictionary containing all observable aircraft parameters is published to a central **DataBus** via an asynchronous publish–subscribe pattern. All monitoring modules subscribe to this bus and evaluate independently, maintaining no shared mutable state. Each module manages its own time-window buffers (typically 5–60 ticks) as required for rate-of-change or trend computations.

**Alert model:** Each module maintains a `_last_alert` field and only broadcasts a notification on state change, providing inherent deduplication, no module produces repeated output during a sustained exceedance. Alerts carry structured payloads compatible with both console output and WebSocket broadcast to external clients:

```json
{
  "id":       "<system-id>",
  "severity": "warning | critical",
  "msg":      "<ECAM/EICAS-style label>",
  "detail":   "<crew guidance text>",
  "topic":    "alert | alert_clear"
}
```

**Phase suppression:** Each module declares a `_SUPPRESS_PHASES` set of flight phases during which evaluation is contextually inappropriate (e.g., approach-specific logic suppressed during ground roll). This prevents spurious alerts outside the operational window of each check.

**State enrichment:** Several modules compute and write derived quantities back into the shared state dictionary (e.g., `glideslope_dots`, `density_alt_ft`, `crosswind_kts`, `fuel_time_remaining_min`, `ice_accumulation`) making them available to downstream modules and Layer 2 without redundant computation.

### 3.3 Flight Simulation

The simulation core (`FlightGenerator`) models a complete flight from gate to gate through eight discrete phases:

```
GROUND_ROLL → ROTATION → CLIMB → CRUISE → DESCENT → APPROACH → LANDING → COMPLETE
```

Dynamics include altitude profile, airspeed, attitude, engine N1/EGT, fuel consumption, atmospheric conditions, QNH, radio altitude, and crew instrument readings. To produce statistically varied training data, the simulator incorporates:

- Sinusoidal cruise altitude wander (±50–150 ft around cruise FL)
- Autothrottle speed hunting (±1–3 kts, 25–100 s period)
- Climb VS noise (5–15 % of nominal)
- 15 % probability of stale barometric subscale settings per flight

### 3.4 Aircraft Performance Database

Aircraft parameters are derived from FAA Type Certificate Data Sheets, published Approved Flight Manuals, Flight Crew Operating Manuals, and open-source JSBSim XML aircraft definitions. Takeoff decision speed (V₁), rotation speed (VR), and takeoff safety speed (V2) are computed from first principles at each simulation instantiation:

```
VS₁ᵍ = sqrt( 2W / (ρ · S · CLmax_TO) )

FAR 25 (transport):  V2 ≥ 1.13·VS₁ᵍ,  VR ≥ 1.05·VS₁ᵍ,  V1 = VR × 0.94
FAR 23 (GA):         V2 = 1.20·VS₁ᵍ,  VR = 1.10·VS₁ᵍ,  V1 = VR − 5 kts
```

Air density at departure elevation is obtained from the **ADRpy** validated ISA atmosphere model when available, with a polynomial fallback.

| Model ID   | Aircraft                  | Category  | Engines | Cruise FL  | Max Range |
|------------|---------------------------|-----------|---------|------------|-----------|
| `c172p`    | Cessna 172P Skyhawk       | FAR 23 GA | 1       | FL085      | 640 nm    |
| `A320`     | Airbus A320-200           | FAR 25    | 2       | FL370      | 3,300 nm  |
| `737`      | Boeing 737-300            | FAR 25    | 2       | FL350      | 2,900 nm  |
| `A330-223` | Airbus A330-223           | FAR 25    | 2       | FL410      | 7,250 nm  |
| `787-8`    | Boeing 787-8 Dreamliner   | FAR 25    | 2       | FL430      | 8,500 nm  |
| `B747`     | Boeing 747-400            | FAR 25    | 4       | FL431      | 8,430 nm  |
| `C130`     | Lockheed C-130 Hercules   | FAR 25    | 4       | FL280      | 2,050 nm  |

### 3.5 Monitoring Module Inventory

#### 3.5.1 Altimetry (ALT)

| ID    | Module                | Description                                                                        |
|-------|-----------------------|------------------------------------------------------------------------------------|
| ALT-1 | Altimeter Cross-Check | Captain / FO altitude indication divergence                                         |
| ALT-2 | Uncommanded Descent   | Altitude loss inconsistent with commanded descent profile                           |
| ALT-3 | Rapid Altitude Loss   | Sudden altitude drop indicative of windshear or structural event                    |
| ALT-4 | Energy State          | Total energy (kinetic + potential) protection                                       |
| ALT-5 | Structural Altitude Rate | Vertical acceleration against structural G-load limits                           |
| ALT-6 | Altimeter Setting     | QNH / STD baro subscale validation; Captain/FO disagree ≥ 5 hPa; ~27 ft per hPa error |

#### 3.5.2 Speed (SPD)

| ID    | Module              | Description                                                       |
|-------|---------------------|-------------------------------------------------------------------|
| SPD-1 | Unreliable Speed    | ADC cross-check across three pitot-static channels               |
| SPD-2 | Overspeed           | VMO / MMO exceedance with Mach-tuck awareness                    |
| SPD-3 | Stall Warning       | Margin to VS₁ᵍ; stick-shaker threshold                           |
| SPD-4 | Low Speed Alert     | Below-VREF during descent                                         |
| SPD-5 | Flap Overspeed      | VFE exceedance at current flap configuration                      |
| SPD-6 | Gear Overspeed      | VLE / VLO exceedance with gear extended or in transit             |
| SPD-7 | Maneuvering Speed   | IAS beyond Va at current weight                                   |

#### 3.5.3 Glide Performance (GLI)

| ID    | Module                | Description                                                       |
|-------|-----------------------|-------------------------------------------------------------------|
| GLI-1 | Glide Range           | Engine-out glide range in nm from current altitude (L/D model)   |
| GLI-2 | Best Glide Speed      | IAS deviation from published best-glide during power-off         |
| GLI-3 | Emergency Descent     | Descent rate adequacy during declared emergency descent           |
| GLI-4 | Glideslope Deviation  | 3° ILS deviation in dots; publishes `glideslope_dots` to state   |
| GLI-5 | VREF Monitor          | Below-VREF with gear extended on approach                         |

#### 3.5.4 Engine (ENG)

| ID    | Module             | Description                                                       |
|-------|--------------------|-------------------------------------------------------------------|
| ENG-1 | N1 Disagree        | Inter-engine N1 spread                                            |
| ENG-2 | EGT Overtemp       | Phase-dependent EGT limits (TOGA / CLB / CRZ)                    |
| ENG-3 | Thrust Asymmetry   | Asymmetric thrust relative to minimum control speed               |
| ENG-4 | Engine Failure     | N1 < 20 % with throttle command > 50 %                           |
| ENG-5 | Efficiency Trend   | 60-tick rolling fuel efficiency, degradation trend               |
| ENG-6 | Compressor Stall   | Rapid simultaneous N1 drop and EGT surge                         |

#### 3.5.5 Attitude (ATT)

| ID    | Module            | Description                                                       |
|-------|-------------------|-------------------------------------------------------------------|
| ATT-1 | Unusual Attitude  | Pitch and bank exceedance beyond standard envelope                |
| ATT-2 | Bank Angle        | Excessive bank; tightened limits below 1,000 ft AGL              |
| ATT-3 | Pitch Limit       | Nose-high / nose-low extremes; phase-adjusted for climb           |
| ATT-4 | Sustained G       | Sustained load factor outside 1 G ± tolerance for > 10 ticks     |
| ATT-5 | Sideslip          | Lateral uncoordinated flight from sideslip or excess yaw rate     |

#### 3.5.6 Pressurization (PRESS)

| ID      | Module                | Description                                                   |
|---------|-----------------------|---------------------------------------------------------------|
| PRESS-1 | Cabin Altitude        | Cabin pressure altitude: WARN 9,000 ft / CRIT 10,000 ft      |
| PRESS-2 | Rapid Decompression   | 5-tick cabin altitude rate consistent with structural breach  |
| PRESS-3 | Differential Pressure | Positive and negative differential pressure limit monitoring  |

#### 3.5.7 Ground Proximity Warning System (GPWS)

| ID     | Module                | Description                                                   |
|--------|-----------------------|---------------------------------------------------------------|
| GPWS-1 | Excessive Sink Rate   | Mode 1, RA-scaled sink rate thresholds                      |
| GPWS-2 | Terrain Closure       | Mode 2, 3-tick radio altitude closure rate                  |
| GPWS-3 | Altitude Loss Takeoff | Mode 3, altitude loss relative to post-liftoff peak         |
| GPWS-4 | Unsafe Configuration  | Mode 4A/4B, gear/flap below minimum safe altitude           |
| GPWS-5 | Glideslope Below      | Mode 5, reads shared `glideslope_dots` from state           |

#### 3.5.8 Approach (APPR)

| ID     | Module             | Description                                                   |
|--------|--------------------|---------------------------------------------------------------|
| APPR-1 | Unstable Approach  | Multi-criteria stabilisation check (speed, VS, path, config) |
| APPR-2 | Low Energy         | Combined low-speed / high-descent-rate below 500 ft AGL      |
| APPR-3 | Crosswind Limit    | Crosswind component from wind speed and relative bearing      |
| APPR-4 | Tailwind           | Tailwind component; publishes `tailwind_kts` to state         |
| APPR-5 | Go-Around Advisory | Weighted scoring across five go-around trigger criteria       |

#### 3.5.9 Icing (ICE)

| ID    | Module             | Description                                                   |
|-------|--------------------|---------------------------------------------------------------|
| ICE-1 | Icing Conditions   | OAT envelope check (−20 °C ≤ T ≤ +5 °C); publishes bool     |
| ICE-2 | Anti-Ice Off       | Anti-ice off in confirmed icing envelope above 10,000 ft      |
| ICE-3 | Ice Accumulation   | Integrator model; publishes `ice_accumulation` to state       |

#### 3.5.10 Fuel (FUEL)

| ID     | Module                 | Description                                                  |
|--------|------------------------|--------------------------------------------------------------|
| FUEL-1 | Fuel Leak Detection    | Unaccounted fuel mass loss inconsistent with power setting   |
| FUEL-2 | Fuel Imbalance         | L/R tank imbalance beyond AFM limits                        |
| FUEL-3 | Fuel Exhaustion        | Time and distance to exhaustion at current burn rate         |
| FUEL-4 | Minimum Diversion Fuel | Remaining fuel vs. ICAO Annex 6 alternate + final reserve    |
| FUEL-5 | Fuel Efficiency        | Specific range vs. 60-tick cruise baseline (WARN < 90 %)    |
| FUEL-6 | Endurance Calculator   | Publishes `endurance_hr` and `endurance_min` to state        |

#### 3.5.11 Performance (PERF)

| ID     | Module               | Description                                                  |
|--------|----------------------|--------------------------------------------------------------|
| PERF-1 | Cruise Altitude      | Actual vs. optimum cruise altitude for weight and conditions |
| PERF-2 | Density Altitude     | ISA deviation and density altitude; takeoff alert if > 2,000 ft excess |
| PERF-3 | Takeoff Performance  | Ground-roll acceleration and V1 attainment monitoring        |

### 3.6 Emergency Fault Injection

The `InjectionManager` applies parametric fault scenarios to the simulation state to produce labelled anomalous records for supervised learning. Faults may be triggered manually via terminal command or applied automatically at a configurable probability (default: 10 % per simulation).

| Fault Injector          | Simulated Failure                                           |
|-------------------------|-------------------------------------------------------------|
| Unreliable Airspeed     | ADC drift on Captain, FO, or both pitot-static channels     |
| Altimeter Disagree      | Altitude ADC divergence between PF and PM instruments       |
| Uncommanded Descent     | Altitude loss not commanded by flight crew                  |
| Rapid Altitude Loss     | Windshear-type altitude excursion                           |
| Energy Bleed            | Simultaneous altitude and speed decay                       |
| Structural G Event      | Sustained G-loading (turbulence / upset)                    |
| Fuel Leak               | Parametric mass loss from individual tanks                  |

### 3.7 Batch Dataset Generation

```bash
python AERIS_ENGINE/batch_sim.py [model] [count] [workers]
```

Generates `count` independent simulations in parallel using `ProcessPoolExecutor`. Each worker is a separate OS process; the GIL is bypassed and random seeds diverge per process. On an 8-core machine, typical speedup is 7–8× relative to sequential generation. Output is self-contained JSON per simulation, with a cumulative `manifest.json` tracking injection statistics.

---

## 4. Layer 2, Subsystem Health Aggregation *(in design)*

Layer 2 aggregates the independent, fine-grained detections from Layer 1 into a structured and explainable **Aircraft Health Model**. Rather than presenting dozens of raw sensor alerts to the reasoning layer, Layer 2 maintains a per-subsystem health assessment, expressed as a confidence-weighted severity score, that reflects the combined state of all monitoring modules within that domain.

The intended design aggregates detections across the following subsystem domains:

- **Propulsion** (ENG-1 through ENG-6, fuel system)
- **Aerodynamics / Flight Envelope** (SPD, ATT, GLI)
- **Avionics / Air Data** (ALT, unreliable speed)
- **Pressurization / Environmental** (PRESS, ICE)
- **Navigation / Approach** (GPWS, APPR)
- **Fuel / Energy** (FUEL, PERF)

This aggregation serves two purposes. First, it reduces the dimensionality of inputs to the AI reasoning layer from individual sensor values to a small set of meaningful subsystem health indicators. Second, and critically, it preserves explainability, each health score carries a provenance chain back to the specific module outputs that contributed to it, enabling the AI layer to generate recommendations grounded in observable evidence rather than opaque feature activations.

---

## 5. Layer 3, Operational Airport Knowledge Base,  GRACE *(deterministic baseline implemented)*

### 5.1 Overview

Layer 3 introduces structured spatial and operational reasoning about available diversion airports. It is implemented as **GRACE,  a Graceful-Relaxation Algorithm for aircraft Emergency Navigation** (`AERIS_ENGINE/decision/diversion_selector.py`), a deterministic, dependency-free ranking engine that combines the FAA NASR airport dataset (runway geometry, classification, NOTAM status) with the aircraft's live state, position, heading, fuel remaining, and an emergency severity tier, to compute an ordered set of feasible diversion candidates.

GRACE is registered on the DataBus as `DiversionDecisionModule` (`AERIS_ENGINE/decision/decision_engine.py`), placed immediately after the Layer 2 aggregator in the subscriber chain (`AERIS_ENGINE/modules/registry.py`) so it consumes the fresh `state["assessment"]` output within the same simulation tick, satisfying the layered dependency shown in Section 2. Its output is published to `state["diversion_recommendation"]` and, when active, rendered live on the AERIS_UI simulate-map screen as ranked overlay markers and a candidate list.

### 5.2 Constraint Model

GRACE separates every consideration into one of two categories, a distinction that is load-bearing to the algorithm's safety behaviour (Section 5.5):

| Category | Examples | Behaviour |
|---|---|---|
| **Physical facts** | NOTAM airport/runway closures; minimum landable runway length | Absolute exclusion. Never relaxed, at any severity or degradation tier. |
| **Safety margins** | Worst-case fuel-burn multiplier; ICAO reserve buffer; turn budget | Configurable per emergency condition. Loosened only as a last resort when no candidate survives at full strictness (Section 5.5), and always disclosed in the result. |

Required runway length is derived from a landing-performance heuristic (`is_transport`, `vref_kts`) and treated as a hard floor at 55 % of that requirement,  under no circumstance, including full graceful-relaxation, does GRACE recommend a runway below that floor.

### 5.3 Multi-Factor Scoring

Each surviving candidate is scored as a weighted sum over six factors (`fn(candidate, context) → [0, 1]`), normalised by the active severity tier's weight profile:

| Factor | Signal | Formula (abbreviated) |
|---|---|---|
| `reachability` | Great-circle distance from current position | `exp(-distance_nm / 150)` |
| `turn` | Heading change required to reach the airport | `1 − turn_deg_scoring / max_turn_deg` |
| `capability` | Best usable runway length vs. required length | `clamp((best_rwy_ft / required_ft) / 1.5, 0, 1)`, +0.15 for large/medium airports under a `prefer_larger_airport` condition |
| `fuel_margin` | Fuel remaining after worst-case burn to reach, minus reserve | `clamp(margin_lbs / fuel_total_lbs, 0, 1)` |
| `availability` | NOTAM cleanliness (0 or partial runway closures) | `1.0` clean, `0.5` if a non-blocking runway closure exists |
| `familiarity` | Is this the departure airport shortly after takeoff, or the destination during a go-around? | `anchor_strength` (0–1; see Section 5.5) |

Note `turn` reads `turn_deg_scoring`, not the true `turn_deg`, an anchored candidate's turn counts for less in scoring even though the true angle is unchanged for the hard turn-capability filter (Section 5.5).

Severity determines which factors dominate,  a HIGH-severity emergency weights proximity, turn, and familiarity heavily ("get down now, somewhere known"); a LOW-severity one weights airport capability more ("there's time to pick a better field"):

| Severity | reachability | turn | capability | fuel_margin | availability | familiarity |
|---|---|---|---|---|---|---|
| `LOW` | 0.15 | 0.10 | **0.40** | 0.15 | 0.20 | 0.10 |
| `MODERATE` | 0.30 | 0.20 | 0.25 | 0.15 | 0.10 | 0.15 |
| `HIGH` | **0.45** | **0.30** | 0.10 | 0.10 | 0.05 | **0.20** |

Turning also costs fuel and time: a candidate's effective diversion distance is inflated by up to 5 % at a full 180° turn before the fuel-reachability check runs, so an aligned airport slightly farther away can outrank a closer one that requires reversing course — unless that closer-but-behind option is the departure airport or a go-around field, per Section 5.5.

### 5.4 Emergency Condition Modifiers

Severity alone does not capture *why* a diversion is needed. GRACE additionally consumes `active_conditions`, a list of alert IDs (the same IDs raised by the Layer 1 monitoring modules), each mapped to a set of context modifiers via an extensible registry:

| Condition ID | Effect |
|---|---|
| `FUEL_LEAK` | 2.0× worst-case fuel-burn assumption; 1.15× reserve buffer |
| `FUEL_EXHAUSTION` | 1.10× fuel burn; 1.25× reserve buffer |
| `FUEL_IMBALANCE`, `THRUST_ASYM` | 1.05× fuel burn (asymmetric drag) |
| `MIN_DIVERT_FUEL` | 1.20× reserve buffer |
| `ENGINE_FAILURE` | 1.10× fuel burn; prefers larger/medium airports (ARFF, longer runways) |
| `ICE_ACCUM` | 1.20× fuel burn (airframe ice drag/weight) |
| `DIRECTIONAL_CONTROL_LOSS` *(illustrative,  not yet raised by a live module)* | Caps turn budget at 45°; prefers larger airports |

This registry is the explicit integration point for Layer 4: a future learned severity classifier, or a new flight-control-failure detector, only needs to raise its alert ID and register a modifier entry,  no change to the scoring or relaxation logic in Sections 5.3 or 5.5 is required. New scoring factors and severity weight profiles are similarly pluggable via `register_factor()` and `set_severity_weights()`.

### 5.5 Known-Airport Anchoring

Distance-and-turn scoring alone systematically undervalues two common, low-risk real-world options that crews are specifically briefed and trained for:

1. **Returning to the departure airport shortly after takeoff.** By the time an early-flight emergency is recognised, the aircraft has typically already turned onto course toward the destination, so the departure airport sits nearly directly *behind* it. A naive turn penalty then scores a strange airport merely ahead of the aircraft *better* than the field it just left, despite "return to departure" being a standard, pre-briefed contingency rather than a novel manoeuvre.
2. **The destination airport during a go-around.** The aircraft is already established near the field and known to ATC; there is essentially never a reason to prefer an unfamiliar airport over attempting the same field again, unless something about that specific airport is the actual problem, which the NOTAM/runway/fuel hard filters (Section 5.2) already catch independently.

GRACE addresses this with an *anchor strength* computed once per query, not per candidate:

| Anchor | Trigger | Strength |
|---|---|---|
| Departure airport | `state["origin_icao"]` | Linear fade from `1.0` at engine start to `0.0` at 20 simulated minutes (`_EARLY_FLIGHT_WINDOW_S`) |
| Destination airport | `GO_AROUND` present in `active_conditions` | `1.0` while active, `0.0` otherwise |

For whichever candidate matches, the anchor strength discounts the angle that counts against it, `turn_deg_scoring = turn_deg × (1 − anchor_strength × 0.85)` (`_ANCHOR_TURN_DISCOUNT = 0.15`), so a fully anchored 180° return-to-field scores as if it were roughly a 27° turn, and directly feeds the `familiarity` factor (Section 5.3) as `anchor_strength` itself.

Critically, anchoring is bounded the same way graceful degradation is (Section 5.6): it never touches the hard turn-*capability* filter (`turn_deg > max_turn_deg` is checked against the true angle, so a genuine directional-control-loss condition still excludes the departure airport if it truly can't be reached) and never touches NOTAM closures, runway length, or fuel reachability. An anchored airport that's closed, too short, or unreachable on fuel is excluded exactly like any other candidate — anchoring only changes how much a *reachable, otherwise-viable* known airport is preferred over an equally reachable unfamiliar one. Every candidate reports `anchor` (`"origin"` / `"go_around"` / `null`) and `anchor_strength` for transparency, and the `reason` string names it explicitly (e.g. *"departure airport — briefed return-to-field option"*).

### 5.6 Graceful Degradation

The algorithm's namesake behaviour: if no airport survives the hard filters at full worst-case strictness, GRACE does not return an empty result. It retries at progressively looser fuel/turn margins, 

```
relaxation levels:  1.0 → 0.7 → 0.4 → 0.15 → 0.0
                    (full worst-case)   (standard ICAO reserve, unrestricted turn)
```

stopping at the first tier that yields a candidate. Per Section 5.2, this relaxation is bounded: it only ever unwinds the *extra* worst-case safety padding back toward the standard reserve and an achievable turn, never below "reach it with a real reserve intact." Runway length and NOTAM closures are excluded from relaxation entirely. Every candidate produced under a relaxed tier carries `relaxed: true` and human-readable `relaxation_notes` (e.g. *"turn limit relaxed 45° → 128°"*), and the recommendation as a whole is flagged `degraded: true`, so a relaxed pick can never be silently mistaken for one that met full margins. If even full relaxation finds nothing reachable, GRACE reports `noReachableAirport: true` rather than fabricating an option.

### 5.7 Live Integration

`DiversionDecisionModule` maps the Layer 2 `overallRisk` classification (`LOW/MODERATE/HIGH/CRITICAL`, where `LOW` means no active alert) down to GRACE's three-tier severity scale, skipping computation entirely while nothing is active. Recomputation is throttled to severity changes or a 15-simulated-second interval, not every 30 Hz tick, since a full airport-dataset scan is unnecessary at that cadence. Output is JSON-serialisable and flows through the same DataBus/WebSocket path as every other module, so it appears in both the live `AERIS_UI` map (ranked diversion markers, click-to-zoom candidate list) and the batch-generated training dataset.

---

## 6. Layer 4, AI Reasoning and Decision Support *(planned)*

Layer 4 is the primary crew-facing advisory component. It receives the integrated aircraft health model from Layer 2 and the airport suitability assessment from Layer 3, and produces structured recommendations addressed to the crew.

The intended capabilities of this layer include:

**Emergency prioritisation**, in multi-failure scenarios, determining which system degradation poses the most immediate flight safety risk and sequencing crew attention accordingly, drawing on the combined health model rather than individual alarm priority.

**Diversion strategy recommendation**, refining the severity classification and condition detection consumed by GRACE (Section 5),  the ranking, relaxation, and rationale generation are already implemented as a deterministic Layer 3 baseline; Layer 4's contribution is a learned classifier feeding GRACE's `severity`/`active_conditions` inputs in place of (or alongside) the current rule-based `overallRisk` mapping, requiring no change to the ranking algorithm itself.

**Adaptive checklist generation**, in scenarios involving simultaneous failures where existing QRH/ECAM procedures are insufficient or conflicting, generating a synthesised abnormal procedure that accounts for the combined failure state rather than addressing each failure in isolation.

**Crew workload awareness**, modulating recommendation frequency and verbosity based on assessed phase criticality, suppressing lower-priority advisories during high-workload segments (e.g., during an active go-around) and surfacing them when cognitive capacity is available.

The AI module is intended to be designed with explainability as a first-order requirement: every recommendation must be traceable to specific sensor values, module outputs, and decision rules, so that the crew can assess and override the recommendation with full situational understanding.

---

## 7. Airport Data

### 7.1 Data Source

Airport and runway data are sourced from the **Federal Aviation Administration (FAA) National Airspace System Resources (NASR)** subscription dataset:

| File              | Records   | Description                                          |
|-------------------|-----------|------------------------------------------------------|
| `APT_BASE.csv`    | ~20,000   | Airport master record, identity, location, operations |
| `APT_RWY.csv`     | ~23,000   | Runway geometry, length, width, surface type        |
| `APT_RWY_END.csv` |,         | Runway-end data, ILS type, TODA, LDA                |

NASR data is published and maintained by the FAA and is freely available at:
**https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/**

### 7.2 Research Use Disclosure

> The FAA NASR airport data files used by AERIS are processed solely for **non-commercial academic and research purposes**. No FAA data is redistributed or incorporated into this repository. Data files must be obtained independently from the FAA NASR subscription service and placed in `AERIS_ENGINE/utils/` before use. Any use of this system with FAA NASR data must comply with the FAA's applicable terms of use. AERIS is not affiliated with, endorsed by, or associated with the Federal Aviation Administration.

### 7.3 Airport Classification

The `faa_airport_loader` module derives an operational classification for each airport from FAA fields for use in route and alternate selection:

| Class           | Criteria                                                                                        |
|-----------------|-------------------------------------------------------------------------------------------------|
| `large_airport` | FAR Part 139 certified + scheduled commercial ops > 0 + paved runway ≥ 7,000 ft               |
| `medium_airport`| Based jets > 0 or commuter ops > 0 + jet fuel available + paved runway ≥ 4,000 ft             |
| `small_airport` | All other public-use, operationally open, paved airports with runway ≥ 1,000 ft               |

Transport-category aircraft are constrained to `large_airport` and `medium_airport` pools for route selection, consistent with airline operational specifications.

---

## 8. Dependencies

| Package       | Purpose                                                 |
|---------------|---------------------------------------------------------|
| Python ≥ 3.11 | Type union syntax, `asyncio`, `dataclasses`             |
| `asyncio`     | Asynchronous DataBus fan-out                            |
| `ADRpy`       | ISA atmosphere, EAS/TAS/CAS conversions *(optional)*    |
| `websockets`  | Real-time alert broadcast to external clients           |

---

## 9. Repository Structure

```
AERIS/
├── AERIS_ENGINE/                  , Layer 1: monitoring and simulation engine
│   ├── core/
│   │   └── data_bus.py            , async pub/sub event bus
│   ├── data/
│   │   └── ingestion/
│   │       ├── FlightGenerator.py , 30 Hz flight state machine
│   │       ├── aircraft_performance.py
│   │       ├── faa_airport_loader.py
│   │       └── notam_reader.py    , ICAO NOTAM text parser/decoder
│   ├── decision/                  , Layer 3: GRACE diversion engine
│   │   ├── diversion_selector.py  , GRACE algorithm (Section 5)
│   │   └── decision_engine.py     , DataBus wiring for GRACE
│   ├── emergencyInjector/         , fault injection framework
│   │   └── notam/                 , randomised NOTAM airport/runway closures
│   ├── modules/
│   │   └── math/                  , 53 analytical monitoring modules
│   │       ├── altimeter_setting.py
│   │       ├── speed/
│   │       ├── glide/
│   │       ├── engine/
│   │       ├── attitude/
│   │       ├── pressurization/
│   │       ├── gpws/
│   │       ├── approach/
│   │       ├── icing/
│   │       ├── fuel/
│   │       └── performance/
│   ├── utils/
│   │   ├── APT_BASE.csv           , FAA NASR (not distributed, obtain from FAA)
│   │   ├── APT_RWY.csv            , FAA NASR (not distributed, obtain from FAA)
│   │   └── APT_RWY_END.csv        , FAA NASR (not distributed, obtain from FAA)
│   ├── simulationdata/            , generated dataset output
│   ├── main.py                    , interactive single-flight runner
│   └── batch_sim.py               , parallel batch dataset generator
└── README.md
```

---

## 10. Regulatory and Data Attribution

**Airport Data:** Airport and runway information is sourced from the FAA National Airspace System Resources (NASR) subscription product, maintained by FAA Aeronautical Information Services. Used exclusively for non-commercial research. Users must obtain NASR data files directly from the FAA and are solely responsible for compliance with the FAA's terms of use.

**Aircraft Performance Data:** Performance parameters are derived from publicly available sources including FAA Type Certificate Data Sheets, Approved Flight Manuals, Flight Crew Operating Manuals, and open-source JSBSim aircraft XML definitions. Values are representative approximations for simulation and research purposes and must not be used for actual flight planning or airworthiness determination.

**Regulatory References:** Alert thresholds reference ICAO Annex 6 (Operation of Aircraft), FAA FAR Part 25 (Transport Category Airplanes), FAR Part 23 (Normal Category Airplanes), and published GPWS/EGPWS operating criteria. These references are cited for transparency and do not constitute regulatory compliance claims.

---

---

## 11. About This Project & Collaboration

AERIS is an independent proof-of-concept developed to demonstrate the technical feasibility of an AI-assisted cockpit decision-support framework. The project is being actively developed as part of a research programme aimed at formal academic affiliation, doctoral study, and grant-funded advancement of the broader system.

If you are a researcher, faculty member, or institution working in the areas of aviation safety, human factors, AI in safety-critical systems, or autonomous decision support, I would very much welcome the opportunity to discuss this work. Potential areas for collaboration or supervision include the AI reasoning architecture (Layer 4), multi-failure diagnostic modelling, human–machine interaction in the cockpit, and dataset development for aviation AI benchmarking.

**Contact:** sanjaykumar73189@gmail.com

---

*AERIS is an independent academic research project. It is not affiliated with, certified by, or endorsed by the Federal Aviation Administration, ICAO, Airbus, Boeing, Lockheed Martin, or any other regulatory authority or aircraft manufacturer. All aircraft performance data is derived from publicly available sources for research purposes only.*
