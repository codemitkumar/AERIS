<div align="center">

# ✈️ AERIS ENGINE

**Adaptive Engine for Real-time Intelligent Simulation**

*Physics-accurate flight simulation and failure injection engine for multi-crew aircraft operations*

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![JSBSim](https://img.shields.io/badge/JSBSim-Flight%20Dynamics-0078D7?style=flat-square)](https://github.com/JSBSim-Team/jsbsim)
[![FlightGear](https://img.shields.io/badge/FlightGear-Visualization-4CAF50?style=flat-square)](https://www.flightgear.org)
[![WebSocket](https://img.shields.io/badge/WebSocket-60%20Hz%20Telemetry-FF6B35?style=flat-square)](https://websockets.readthedocs.io)
[![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)

</div>

---

## Overview

AERIS Engine is a high-fidelity flight simulation backend that models complete takeoff sequences from brake release through cruise climb at **60 Hz precision**. It integrates JSBSim physics, dual Air Data Computer (ADC) simulation, dynamic failure injection, and real-time WebSocket telemetry — built for training, analysis, and AI-driven crew decision research.

```
Brake Release → Ground Roll → Rotation → Initial Climb → Climb → Level-Off → Cruise
```

---

## Features

| Category | Details |
|---|---|
| **Aircraft Models** | Cessna C172P · Airbus A320 / A330 · Boeing 737 / 747 / 787 · Lockheed C130 |
| **Simulation Rate** | 60 Hz physics loop with asyncio |
| **Failure Modes** | Pitot icing · Pitot blockage · Static port blockage · ADC failures |
| **Visualization** | FlightGear via UDP at 60 Hz |
| **Telemetry** | WebSocket broadcast with real-time state and command injection |
| **ADC Channels** | Independent Captain + First Officer channels with sensor noise |

---

## Architecture

```
AERIS_ENGINE/
├── main.py                    # Entry point — launches FlightGear & async sim loop
│
├── core/
│   ├── engine.py              # Main simulation orchestrator
│   ├── takeoff_manager.py     # 6-phase flight control logic
│   ├── scheduler.py           # 60 Hz async task scheduler
│   └── state_manager.py       # Shared flight state store
│
├── data/
│   └── ingestion/
│       ├── jsbsim_client.py   # JSBSim physics interface
│       ├── aircraft_performance.py  # V-speeds, TOW, performance tables
│       └── dual_adc.py        # Dual ADC with failure mode simulation
│
├── communication/
│   ├── websocket_server.py    # Real-time telemetry & command handler
│   └── flightgear_sender.py   # UDP bridge to FlightGear
│
├── decision/
│   ├── alert_rules.py         # Crew alert rule definitions
│   └── decision_engine.py     # AI decision stub (extensible)
│
└── config/
    ├── settings.py            # Global simulation constants
    └── flightgear.xml         # FlightGear UDP output configuration
```

---

## Getting Started

### Prerequisites

- Python 3.10+
- [JSBSim](https://github.com/JSBSim-Team/jsbsim) (`pip install jsbsim`)
- [FlightGear](https://www.flightgear.org/download/) (for visualization)
- `websockets` (`pip install websockets`)

### Installation

```bash
git clone https://github.com/codemitkumar/AERIS.git
cd AERIS/AERIS_ENGINE
pip install -r requirements.txt
```

### Running the Simulation

```bash
# Start with default aircraft (Cessna C172P)
python main.py

# Start with a specific aircraft
python main.py --aircraft A320
```

FlightGear will launch automatically and the WebSocket server will begin streaming telemetry on `ws://localhost:8765`.

---

## Supported Aircraft

| Aircraft | Type | Engines |
|---|---|---|
| Cessna C172P | General Aviation | 1× Piston |
| Airbus A320 | Narrow-body Jet | 2× Turbofan |
| Airbus A330 | Wide-body Jet | 2× Turbofan |
| Boeing 737 | Narrow-body Jet | 2× Turbofan |
| Boeing 747 | Wide-body Jet | 4× Turbofan |
| Boeing 787 | Wide-body Jet | 2× Turbofan |
| Lockheed C130 | Military Transport | 4× Turboprop |

---

## Failure Injection

Inject failures at runtime via WebSocket commands:

```json
{
  "command": "inject_failure",
  "type": "pitot_icing",
  "channel": "captain"
}
```

| Failure Type | Description |
|---|---|
| `pitot_icing` | Gradual pitot tube icing — airspeed degrades over time |
| `pitot_blockage` | Sudden pitot tube blockage — frozen airspeed reading |
| `static_port_blockage` | Blocked static port — altitude/VSI errors |
| `adc_failure` | Full ADC channel failure — all derived data lost |

Both **Captain** and **First Officer** ADC channels can be failed independently to simulate split-crew scenarios.

---

## WebSocket Telemetry

Connect to `ws://localhost:8765` to receive real-time flight state at 60 Hz:

```json
{
  "phase": "climb",
  "altitude_ft": 1420.5,
  "airspeed_kts": 168.3,
  "pitch_deg": 12.1,
  "roll_deg": 0.4,
  "vspeed_fpm": 1800,
  "captain_adc": { "valid": true, "airspeed": 168.3 },
  "fo_adc": { "valid": true, "airspeed": 167.9 }
}
```

---

## Flight Phases

```
Phase 1  GROUND ROLL    — Throttle-up, directional control
Phase 2  ROTATION       — Pitch to VR, nose-gear lift-off
Phase 3  INITIAL CLIMB  — Gear retraction, flap schedule begins
Phase 4  CLIMB          — Accelerate to V2+10, climb power
Phase 5  LEVEL-OFF      — Transition to cruise altitude
Phase 6  CRUISE         — Stabilized level flight, data collection
```

---

## Extending the Decision Engine

The `decision/decision_engine.py` module is an extensible stub ready for AI integration:

```python
class DecisionEngine:
    def evaluate(self, state: FlightState) -> Optional[CrewAlert]:
        # Plug in your ML model, rule engine, or LLM here
        ...
```

---

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature`
3. Commit your changes: `git commit -m "Add your feature"`
4. Push and open a Pull Request

---

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.

---

<div align="center">

Built with JSBSim · FlightGear · Python asyncio

*AERIS — Adaptive Engine for Real-time Intelligent Simulation*

</div>
