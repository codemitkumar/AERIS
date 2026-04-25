import { useState, useEffect, useRef, useCallback } from "react";
import "./App.css";

import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import * as THREE from "three";

function loadAircraftModel() {
  return new Promise((resolve, reject) => {
    const loader = new GLTFLoader();
    loader.load(
      "/models/a330/Master.gltf",
      (gltf) => resolve(extractGeometry(gltf.scene)),
      undefined,
      reject
    );
  });
}

function extractGeometry(scene) {
  const verts = [], norms = [], cols = [];
  scene.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(scene);
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  const SCALE = 4 / Math.max(size.x, size.y, size.z);

  scene.traverse((node) => {
    if (!node.isMesh) return;
    let geometry = node.geometry;
    if (geometry.index) geometry = geometry.toNonIndexed();
    const position = geometry.attributes.position;
    const normal = geometry.attributes.normal;
    const color = new THREE.Color(0.8, 0.8, 0.85);
    const v = new THREE.Vector3(), n = new THREE.Vector3();
    const matrix = node.matrixWorld;
    for (let i = 0; i < position.count; i++) {
      v.fromBufferAttribute(position, i).applyMatrix4(matrix);
      verts.push((v.x - center.x) * SCALE, (v.y - center.y) * SCALE, (v.z - center.z) * SCALE);
      if (normal) {
        n.fromBufferAttribute(normal, i).transformDirection(matrix);
        norms.push(n.x, n.y, n.z);
      } else {
        norms.push(0, 1, 0);
      }
      cols.push(color.r, color.g, color.b);
    }
    if (node.geometry.index) geometry.dispose();
  });
  return { verts: new Float32Array(verts), norms: new Float32Array(norms), cols: new Float32Array(cols), count: verts.length / 3 };
}

// ─── AERIS WebSocket Hook ─────────────────────────────────────────────────────
const _DEFAULT_STATE = {
  // Primary display (derived from most reliable channel)
  bankAngle: 0, pitchAngle: 0, headingAngle: 0,
  airspeed: 0, altitude: 0, verticalSpeed: 0, groundspeed: 0,
  // Dual-channel ADC
  iasCaptain: 0, iasFo: 0,
  altCaptain: 0, altFo: 0,
  vsCaptain: 0,  vsFo: 0,
  // Dual-channel AHRS
  pitchCaptain: 0, pitchFo: 0,
  rollCaptain: 0,  rollFo: 0,
  headingCaptain: 0, headingFo: 0,
  // Validity & failure
  adcCaptainValid: true, adcFoValid: true,
  ahrsCapValid: true,    ahrsFoValid: true,
  adcCaptainFailure: "none", adcFoFailure: "none",
  // Cross-check & disagree
  anyDisagree: false, altDisagree: false,
  pitchDisagree: false, rollDisagree: false,
  iasDelta: 0, altDelta: 0,
  // Suspect flags & guidance
  captainSuspect: false, foSuspect: false, bothSuspect: false,
  recommendedAction: "",
  // Flight phase & V-speeds
  phase: "GROUND_ROLL", v1: 0, vr: 0, v2: 0,
  // Alert helpers
  stall: false, overspeed: false, gpws: false, tcas: false, stallNorm: 0,
  // Unused legacy fields kept for component compatibility
  alphaAngle: 0, betaAngle: 0,
  rollRate: 0, pitchRate: 0, yawRate: 0,
  engineN1L: 0, engineN1R: 0, engineN2L: 0, engineN2R: 0,
  engineRPM0: 0, fuelLeft: 0, fuelRight: 0,
  hydraulicA: 3000, hydraulicB: 3000, apu: false, gearDown: false,
  flapAngle: 0, spoilers: false, autoThrottle: false, autopilot: false, pitotHeat: false,
};

function useJSBSimData() {
  const [data, setData] = useState(_DEFAULT_STATE);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef(null);
  const timerRef = useRef(null);

  useEffect(() => {
    function connect() {
      try { wsRef.current?.close(); } catch (_) {}
      const ws = new WebSocket("ws://localhost:8765");
      wsRef.current = ws;

      ws.onopen = () => { setConnected(true); clearTimeout(timerRef.current); };
      ws.onclose = () => { setConnected(false); timerRef.current = setTimeout(connect, 2000); };
      ws.onerror = () => ws.close();

      ws.onmessage = ({ data: raw }) => {
        try {
          const s = JSON.parse(raw);

          // Dual-channel values from AERIS FlightDataAdapter
          const pitchC = s.pitch_captain  ?? 0;
          const rollC  = s.roll_captain   ?? 0;
          const hdgC   = s.heading_captain ?? 0;
          const iasC   = s.ias_captain    ?? 0;
          const altC   = s.alt_captain    ?? 0;
          const vsC    = s.vs_captain     ?? 0;

          const pitchFO = s.pitch_fo  ?? 0;
          const rollFO  = s.roll_fo   ?? 0;
          const hdgFO   = s.heading_fo ?? 0;
          const iasFO   = s.ias_fo    ?? 0;
          const altFO   = s.alt_fo    ?? 0;
          const vsFO    = s.vs_fo     ?? 0;

          const capSuspect = s.captain_suspect ?? false;
          const foSuspect  = s.fo_suspect      ?? false;

          // Primary display: use FO channel if captain is suspect (and FO is not)
          const usefo = capSuspect && !foSuspect;
          const bank  = usefo ? rollFO  : rollC;
          const pitch = usefo ? pitchFO : pitchC;
          const hdg   = usefo ? hdgFO   : hdgC;
          const ias   = usefo ? iasFO   : iasC;
          const alt   = usefo ? altFO   : altC;
          const vs    = usefo ? vsFO    : vsC;

          setData({
            bankAngle:    bank,
            pitchAngle:   pitch,
            headingAngle: hdg,
            airspeed:     ias,
            altitude:     alt,
            verticalSpeed: vs,
            groundspeed:  s.groundspeed_kts ?? 0,

            iasCaptain: iasC,   iasFo: iasFO,
            altCaptain: altC,   altFo: altFO,
            vsCaptain:  vsC,    vsFo:  vsFO,
            pitchCaptain: pitchC, pitchFo: pitchFO,
            rollCaptain:  rollC,  rollFo:  rollFO,
            headingCaptain: hdgC, headingFo: hdgFO,

            adcCaptainValid:   s.adc_captain_valid   ?? true,
            adcFoValid:        s.adc_fo_valid         ?? true,
            ahrsCapValid:      s.ahrs_captain_valid   ?? true,
            ahrsFoValid:       s.ahrs_fo_valid        ?? true,
            adcCaptainFailure: s.adc_captain_failure  ?? "none",
            adcFoFailure:      s.adc_fo_failure       ?? "none",

            anyDisagree:   s.any_disagree   ?? false,
            altDisagree:   s.alt_disagree   ?? false,
            pitchDisagree: s.pitch_disagree ?? false,
            rollDisagree:  s.roll_disagree  ?? false,
            iasDelta:      s.ias_delta_kts  ?? 0,
            altDelta:      s.alt_delta_ft   ?? 0,

            captainSuspect:    capSuspect,
            foSuspect:         foSuspect,
            bothSuspect:       s.both_suspect      ?? false,
            recommendedAction: s.recommended_action ?? "",

            phase: s.phase  ?? "GROUND_ROLL",
            v1:    s.v1_kts ?? 0,
            vr:    s.vr_kts ?? 0,
            v2:    s.v2_kts ?? 0,

            stall:     ias > 0 && ias < 45,
            overspeed: ias > 250,
            gpws:      Math.abs(bank) > 45,
            tcas:      false,
            stallNorm: 0,

            alphaAngle: 0, betaAngle: 0,
            rollRate: 0, pitchRate: 0, yawRate: 0,
            engineN1L: 0, engineN1R: 0, engineN2L: 0, engineN2R: 0,
            engineRPM0: 0, fuelLeft: 0, fuelRight: 0,
            hydraulicA: 3000, hydraulicB: 3000, apu: false,
            gearDown: false, flapAngle: 0,
            spoilers: false, autoThrottle: false, autopilot: false, pitotHeat: false,
          });
        } catch (_) {}
      };
    }

    connect();
    return () => { clearTimeout(timerRef.current); wsRef.current?.close(); };
  }, []);

  return { data, connected };
}

// ─── Alert definitions ────────────────────────────────────────────────────────
function getAlerts(fd) {
  const alerts = [];

  // AERIS sensor disagree / suspect alerts (highest priority)
  if (fd.bothSuspect)
    alerts.push({ id: "UNRELIABLE_SPD", severity: "critical", msg: "UNRELIABLE AIRSPEED",
      detail: fd.recommendedAction || "Both ADC channels suspect — cross-check all instruments" });
  if (fd.captainSuspect && !fd.bothSuspect)
    alerts.push({ id: "CAPT_ADC", severity: "warning", msg: "CAPT ADC SUSPECT",
      detail: `${fd.adcCaptainFailure || "Failure detected"} — cross-check F/O instruments` });
  if (fd.foSuspect && !fd.bothSuspect)
    alerts.push({ id: "FO_ADC", severity: "warning", msg: "F/O ADC SUSPECT",
      detail: `${fd.adcFoFailure || "Failure detected"} — cross-check CAPT instruments` });
  if (fd.pitchDisagree)
    alerts.push({ id: "PITCH_DISAGREE", severity: "critical", msg: "PITCH DISAGREE",
      detail: `AHRS mismatch Δ${Math.abs(fd.pitchCaptain - fd.pitchFo).toFixed(1)}° — cross-check attitude` });
  if (fd.rollDisagree)
    alerts.push({ id: "ROLL_DISAGREE", severity: "critical", msg: "ROLL DISAGREE",
      detail: `AHRS mismatch Δ${Math.abs(fd.rollCaptain - fd.rollFo).toFixed(1)}° — cross-check attitude` });
  if (fd.altDisagree)
    alerts.push({ id: "ALT_DISAGREE", severity: "warning", msg: "ALT DISAGREE",
      detail: `ADC alt delta ${Math.abs(fd.altDelta).toFixed(0)} ft — verify altimeter settings` });

  // Standard attitude alerts
  if (fd.gpws)
    alerts.push({ id: "BANK",   severity: "critical", msg: "BANK ANGLE",   detail: "Excessive bank — reduce roll immediately" });
  if (fd.pitchAngle > 15)
    alerts.push({ id: "NOSEUP", severity: "warning",  msg: "NOSE HIGH",    detail: `Pitch ${fd.pitchAngle.toFixed(1)}° — reduce back pressure` });
  if (fd.pitchAngle < -10)
    alerts.push({ id: "NOSDN",  severity: "warning",  msg: "NOSE LOW",     detail: `Pitch ${fd.pitchAngle.toFixed(1)}° — increase back pressure` });
  if (fd.airspeed > 0 && fd.airspeed < 52)
    alerts.push({ id: "LOWSPD", severity: "warning",  msg: "LOW AIRSPEED", detail: `IAS ${fd.airspeed.toFixed(0)} kts — near stall speed` });
  if (fd.tcas)
    alerts.push({ id: "TCAS",   severity: "advisory", msg: "TCAS RA",      detail: "Traffic advisory — climb" });
  return alerts;
}

// ─── Artificial Horizon (SVG) ─────────────────────────────────────────────────
function ArtificialHorizon({ bankAngle, pitchAngle }) {
  const SIZE = 148;
  const r = SIZE / 2;
  const PX_PER_DEG = SIZE / 48; // 48° spans full height
  const offset = -pitchAngle * PX_PER_DEG;  // positive pitch = nose up = horizon moves down

  const bankTicks = [-60, -45, -30, -20, -10, 0, 10, 20, 30, 45, 60];

  // Triangle indicator pointing inward from the bank scale arc
  const triRad = (-bankAngle - 90) * Math.PI / 180;
  const arcR = r - 9;
  const tx = r + arcR * Math.cos(triRad);
  const ty = r + arcR * Math.sin(triRad);
  const nx = Math.cos(triRad), ny = Math.sin(triRad);
  const px = -ny * 5, py = nx * 5;

  return (
    <svg width={SIZE} height={SIZE} style={{ display: "block", margin: "0 auto" }}>
      <defs>
        <clipPath id="ah-clip"><circle cx={r} cy={r} r={r - 10} /></clipPath>
      </defs>

      {/* Rotating attitude ball */}
      <g transform={`rotate(${-bankAngle}, ${r}, ${r})`} clipPath="url(#ah-clip)">
        <rect x={-SIZE} y={-SIZE * 2 + r - offset} width={SIZE * 5} height={SIZE * 3} fill="#0a2540" />
        <rect x={-SIZE} y={r - offset} width={SIZE * 5} height={SIZE * 3} fill="#3d1e08" />
        <line x1={-SIZE} y1={r - offset} x2={SIZE * 3} y2={r - offset} stroke="#dde8f0" strokeWidth="2.5" />
        {/* Pitch graduation marks */}
        {[-20, -10, 10, 20].map(deg => {
          const yy = r - offset - deg * PX_PER_DEG;
          const halfW = deg % 20 === 0 ? 22 : 14;
          return (
            <g key={deg}>
              <line x1={r - halfW} y1={yy} x2={r + halfW} y2={yy} stroke="rgba(220,230,240,0.55)" strokeWidth="1.5" />
              <text x={r + halfW + 3} y={yy + 4} fill="rgba(180,200,220,0.5)" fontSize="7" fontFamily="monospace">{Math.abs(deg)}</text>
            </g>
          );
        })}
      </g>

      {/* Bank angle scale ticks (fixed) */}
      {bankTicks.map(deg => {
        const rad = (deg - 90) * Math.PI / 180;
        const inner = r - 9;
        const outer = r - 9 - (deg % 30 === 0 ? 10 : 6);
        return (
          <line key={deg}
            x1={r + inner * Math.cos(rad)} y1={r + inner * Math.sin(rad)}
            x2={r + outer * Math.cos(rad)} y2={r + outer * Math.sin(rad)}
            stroke={deg === 0 ? "#60aadd" : "#2a4060"}
            strokeWidth={deg === 0 ? 2.5 : 1}
          />
        );
      })}

      {/* Moving bank triangle */}
      <polygon
        points={`${tx},${ty} ${tx - nx * 9 + px},${ty - ny * 9 + py} ${tx - nx * 9 - px},${ty - ny * 9 - py}`}
        fill="#ffcc00"
      />

      {/* Fixed miniature aircraft symbol */}
      <line x1={r - 36} y1={r} x2={r - 14} y2={r} stroke="#ffcc00" strokeWidth="3" strokeLinecap="round" />
      <line x1={r + 14} y1={r} x2={r + 36} y2={r} stroke="#ffcc00" strokeWidth="3" strokeLinecap="round" />
      <line x1={r - 5}  y1={r + 9} x2={r + 5} y2={r + 9} stroke="#ffcc00" strokeWidth="2.5" strokeLinecap="round" />
      <circle cx={r} cy={r} r={2.5} fill="#ffcc00" />

      {/* Outer bezel */}
      <circle cx={r} cy={r} r={r - 2} fill="none" stroke="#0d1e30" strokeWidth="5" />
      <circle cx={r} cy={r} r={r - 2} fill="none" stroke="#1a3a50" strokeWidth="1" />
    </svg>
  );
}

// ─── Attitude Reference Panel (always-visible right sidebar) ──────────────────
function AttitudeReferencePanel({ fd, connected }) {
  const bank  = fd.bankAngle;
  const pitch = fd.pitchAngle;

  const bankColor  = Math.abs(bank) > 45 ? "#ff3333" : Math.abs(bank) > 25 ? "#ffaa00" : "#40e0a0";
  const pitchColor = (pitch > 15 || pitch < -10) ? "#ff3333" : Math.abs(pitch) > 8 ? "#ffaa00" : "#40e0a0";
  const unusualAttitude = Math.abs(bank) > 30 || pitch > 15 || pitch < -10;

  const Lbl = ({ children }) => (
    <div style={{ fontSize: 7, color: "#2a4060", letterSpacing: 2, fontFamily: "'Share Tech Mono',monospace", marginBottom: 1 }}>
      {children}
    </div>
  );

  const BigNum = ({ val, decimals = 1, color, unit, fontSize = 20 }) => (
    <div style={{ display: "flex", alignItems: "baseline", gap: 3, lineHeight: 1 }}>
      <span style={{ fontFamily: "'Share Tech Mono',monospace", fontSize, fontWeight: 700, color, letterSpacing: 1 }}>
        {(val >= 0 ? "+" : "") + val.toFixed(decimals)}
      </span>
      <span style={{ color: "#2a4060", fontSize: 10 }}>{unit}</span>
    </div>
  );

  const hdg = ((fd.headingAngle % 360) + 360) % 360;

  // Channel health colours
  const capColor = fd.captainSuspect ? "#ff4444" : fd.adcCaptainValid && fd.ahrsCapValid ? "#40e0a0" : "#ffaa00";
  const foColor  = fd.foSuspect      ? "#ff4444" : fd.adcFoValid     && fd.ahrsFoValid   ? "#40e0a0" : "#ffaa00";

  const DualRow = ({ lbl, capVal, foVal, unit, disagree, decimals = 1 }) => (
    <div style={{ marginBottom: 4 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 1 }}>
        <span style={{ fontSize: 7, color: disagree ? "#ff9900" : "#1a3050", fontFamily: "monospace", letterSpacing: 2 }}>
          {lbl}{disagree ? " !" : ""}
        </span>
        <span style={{ fontSize: 7, color: "#1a2a3a", fontFamily: "monospace" }}>{unit}</span>
      </div>
      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <span style={{ fontSize: 11, color: capColor, fontFamily: "monospace", fontWeight: 600 }}>
          C {capVal.toFixed(decimals)}
        </span>
        <span style={{ fontSize: 11, color: foColor, fontFamily: "monospace", fontWeight: 600 }}>
          F {foVal.toFixed(decimals)}
        </span>
      </div>
    </div>
  );

  return (
    <div style={{
      width: 210, flexShrink: 0,
      background: "#030a14",
      borderLeft: "1px solid #0d1e30",
      display: "flex", flexDirection: "column",
      padding: "10px 10px 8px",
      gap: 7,
      overflowY: "auto",
    }}>

      {/* Header */}
      <div style={{ textAlign: "center", borderBottom: "1px solid #0d1a28", paddingBottom: 7 }}>
        <div style={{ fontSize: 9, color: "#3a6090", letterSpacing: 4, fontFamily: "'Share Tech Mono',monospace" }}>
          AERIS · ATTITUDE REF
        </div>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 5, marginTop: 4 }}>
          <div style={{ width: 5, height: 5, borderRadius: "50%", background: connected ? "#00cc55" : "#cc3333", boxShadow: connected ? "0 0 5px #00cc55" : "none" }} />
          <span style={{ fontSize: 7, color: connected ? "#00aa44" : "#aa2222", letterSpacing: 2, fontFamily: "monospace" }}>
            {connected ? "ENGINE LIVE" : "NO SIGNAL"}
          </span>
        </div>
      </div>

      {/* Flight phase & V-speeds */}
      <div style={{ borderBottom: "1px solid #0d1a28", paddingBottom: 7 }}>
        <div style={{ fontSize: 8, color: "#3a6090", letterSpacing: 3, fontFamily: "monospace", marginBottom: 4 }}>
          {fd.phase.replace("_", " ")}
        </div>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          {[["V1", fd.v1], ["VR", fd.vr], ["V2", fd.v2]].map(([lbl, val]) => (
            <div key={lbl} style={{ textAlign: "center" }}>
              <div style={{ fontSize: 7, color: "#2a4060", fontFamily: "monospace" }}>{lbl}</div>
              <div style={{ fontSize: 11, color: "#5090c0", fontFamily: "monospace", fontWeight: 600 }}>{val.toFixed(0)}</div>
            </div>
          ))}
          <div style={{ textAlign: "center" }}>
            <div style={{ fontSize: 7, color: "#2a4060", fontFamily: "monospace" }}>GS</div>
            <div style={{ fontSize: 11, color: "#5090c0", fontFamily: "monospace", fontWeight: 600 }}>{fd.groundspeed.toFixed(0)}</div>
          </div>
        </div>
      </div>

      {/* Artificial Horizon */}
      <div>
        <ArtificialHorizon bankAngle={bank} pitchAngle={pitch} />
      </div>

      {/* Primary attitude readouts */}
      <div style={{ borderTop: "1px solid #0d1a28", paddingTop: 7, display: "flex", flexDirection: "column", gap: 6 }}>
        <div>
          <Lbl>BANK (ROLL)</Lbl>
          <BigNum val={bank} color={bankColor} unit="°" />
          <div style={{ height: 3, background: "#091520", borderRadius: 2, marginTop: 3, position: "relative", overflow: "hidden" }}>
            <div style={{ position: "absolute", left: "50%", width: `${Math.min(50, Math.abs(bank) / 90 * 100)}%`, transform: bank >= 0 ? "none" : "translateX(-100%)", height: "100%", background: bankColor, borderRadius: 2, transition: "width 0.1s" }} />
            <div style={{ position: "absolute", left: "50%", top: 0, width: 1, height: "100%", background: "#1a3050" }} />
          </div>
        </div>
        <div>
          <Lbl>PITCH</Lbl>
          <BigNum val={pitch} color={pitchColor} unit="°" />
        </div>
        <div>
          <Lbl>HEADING (TRUE)</Lbl>
          <div style={{ display: "flex", alignItems: "baseline", gap: 3 }}>
            <span style={{ fontFamily: "'Share Tech Mono',monospace", fontSize: 18, fontWeight: 700, color: "#70aadd", letterSpacing: 1 }}>
              {hdg.toFixed(1)}
            </span>
            <span style={{ color: "#2a4060", fontSize: 10 }}>°</span>
          </div>
        </div>
      </div>

      {/* Dual-channel ADC */}
      <div style={{ borderTop: "1px solid #0d1a28", paddingTop: 6 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 5 }}>
          <Lbl>DUAL ADC</Lbl>
          <div style={{ display: "flex", gap: 6 }}>
            <span style={{ fontSize: 7, color: capColor, fontFamily: "monospace" }}>
              CAPT {fd.adcCaptainFailure !== "none" ? `[${fd.adcCaptainFailure}]` : "OK"}
            </span>
            <span style={{ fontSize: 7, color: foColor, fontFamily: "monospace" }}>
              F/O {fd.adcFoFailure !== "none" ? `[${fd.adcFoFailure}]` : "OK"}
            </span>
          </div>
        </div>
        <DualRow lbl="IAS" capVal={fd.iasCaptain} foVal={fd.iasFo} unit="kts" disagree={fd.anyDisagree} decimals={0} />
        <DualRow lbl="ALT" capVal={fd.altCaptain} foVal={fd.altFo} unit="ft"  disagree={fd.altDisagree} decimals={0} />
        <DualRow lbl="V/S" capVal={fd.vsCaptain}  foVal={fd.vsFo}  unit="fpm" disagree={false} decimals={0} />
      </div>

      {/* Dual-channel AHRS */}
      <div style={{ borderTop: "1px solid #0d1a28", paddingTop: 6 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 5 }}>
          <Lbl>DUAL AHRS</Lbl>
          <div style={{ display: "flex", gap: 6 }}>
            <span style={{ fontSize: 7, color: fd.ahrsCapValid ? "#40e0a0" : "#ff4444", fontFamily: "monospace" }}>
              CAPT {fd.ahrsCapValid ? "OK" : "FAIL"}
            </span>
            <span style={{ fontSize: 7, color: fd.ahrsFoValid ? "#40e0a0" : "#ff4444", fontFamily: "monospace" }}>
              F/O {fd.ahrsFoValid ? "OK" : "FAIL"}
            </span>
          </div>
        </div>
        <DualRow lbl="PITCH" capVal={fd.pitchCaptain} foVal={fd.pitchFo} unit="°"   disagree={fd.pitchDisagree} />
        <DualRow lbl="ROLL"  capVal={fd.rollCaptain}  foVal={fd.rollFo}  unit="°"   disagree={fd.rollDisagree} />
        <DualRow lbl="HDG"   capVal={fd.headingCaptain} foVal={fd.headingFo} unit="°" disagree={false} decimals={0} />
      </div>

      {/* Recommended action */}
      {fd.recommendedAction && (
        <div style={{ borderTop: "1px solid #0d1a28", paddingTop: 6 }}>
          <Lbl>RECOMMENDED ACTION</Lbl>
          <div style={{ fontSize: 8, color: "#ffaa00", fontFamily: "monospace", lineHeight: 1.5, marginTop: 2 }}>
            {fd.recommendedAction}
          </div>
        </div>
      )}

      {/* Spatial Disorientation / status footer */}
      <div style={{ marginTop: "auto", borderTop: "1px solid #0d1a28", paddingTop: 7, textAlign: "center" }}>
        {unusualAttitude ? (
          <div style={{
            background: "rgba(255,30,30,0.10)", border: "1px solid rgba(255,30,30,0.50)",
            borderRadius: 5, padding: "7px 6px",
            animation: "alertPulse 0.6s ease-in-out infinite alternate",
          }}>
            <div style={{ fontSize: 9, color: "#ff4444", letterSpacing: 2, fontWeight: 700, fontFamily: "monospace", marginBottom: 3 }}>
              UNUSUAL ATTITUDE
            </div>
            <div style={{ fontSize: 7, color: "#cc3333", letterSpacing: 1, lineHeight: 1.5, fontFamily: "monospace" }}>
              TRUST YOUR INSTRUMENTS<br />DISREGARD BODY SENSATIONS
            </div>
          </div>
        ) : (
          <div style={{ fontSize: 7, color: "#1a3050", letterSpacing: 1, lineHeight: 1.7, fontFamily: "monospace" }}>
            TRUST YOUR INSTRUMENTS<br />AVIATE · NAVIGATE · COMMUNICATE
          </div>
        )}
      </div>
    </div>
  );
}

// ─── 3D Aircraft WebGL Renderer ───────────────────────────────────────────────
function AircraftViewer({ flightData, showAlerts, modelData }) {
  const canvasRef  = useRef(null);
  const glRef      = useRef(null);
  const programRef = useRef(null);
  const buffersRef = useRef(null);
  const animRef    = useRef(null);

  const buildProgram = useCallback((gl) => {
    const vs = `attribute vec3 aPos;attribute vec3 aNorm;attribute vec3 aColor;uniform mat4 uMVP;uniform mat4 uModel;varying vec3 vNorm;varying vec3 vColor;void main(){vNorm=normalize(mat3(uModel)*aNorm);vColor=aColor;gl_Position=uMVP*vec4(aPos,1.0);}`;
    const fs = `precision mediump float;varying vec3 vNorm;varying vec3 vColor;uniform vec3 uLight;uniform float uAlertPulse;void main(){float d=max(dot(normalize(vNorm),normalize(uLight)),0.0)*0.65+0.35;vec3 c=vColor*d;c=mix(c,vec3(1.0,0.08,0.08),uAlertPulse*0.4);gl_FragColor=vec4(c,1.0);}`;
    const compile = (type, src) => { const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); return s; };
    const prog = gl.createProgram();
    gl.attachShader(prog, compile(gl.VERTEX_SHADER, vs));
    gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, fs));
    gl.linkProgram(prog);
    return prog;
  }, []);

  const buildFallbackGeo = useCallback(() => {
    const verts = [], norms = [], cols = [];
    const push = (x, y, z, nx, ny, nz, r, g, bv) => { verts.push(x, y, z); norms.push(nx, ny, nz); cols.push(r, g, bv); };
    const quad = (pts, nx, ny, nz, r, g, bv) => {
      const [p0, p1, p2, p3] = pts;
      push(...p0, nx, ny, nz, r, g, bv); push(...p1, nx, ny, nz, r, g, bv); push(...p2, nx, ny, nz, r, g, bv);
      push(...p0, nx, ny, nz, r, g, bv); push(...p2, nx, ny, nz, r, g, bv); push(...p3, nx, ny, nz, r, g, bv);
    };
    const tube = (cx, cy, cz, len, ry, segs, r, g, bv) => {
      for (let i = 0; i < segs; i++) {
        const a0 = i / segs * Math.PI * 2, a1 = (i + 1) / segs * Math.PI * 2;
        const y0 = Math.cos(a0) * ry, z0 = Math.sin(a0) * ry, y1 = Math.cos(a1) * ry, z1 = Math.sin(a1) * ry;
        const xf = cx + len / 2, xb = cx - len / 2;
        push(xf, cy + y0, cz + z0, Math.cos(a0), 0, Math.sin(a0), r, g, bv); push(xf, cy + y1, cz + z1, Math.cos(a1), 0, Math.sin(a1), r, g, bv); push(xb, cy + y1, cz + z1, Math.cos(a1), 0, Math.sin(a1), r, g, bv);
        push(xf, cy + y0, cz + z0, Math.cos(a0), 0, Math.sin(a0), r, g, bv); push(xb, cy + y1, cz + z1, Math.cos(a1), 0, Math.sin(a1), r, g, bv); push(xb, cy + y0, cz + z0, Math.cos(a0), 0, Math.sin(a0), r, g, bv);
      }
    };
    const wing = (x0, x1, y0, y1, tz, r, g, bv) => {
      const th = 0.08;
      quad([[x0, y0, tz + th], [x1, y1, tz + th], [x1, y1, tz - th], [x0, y0, tz - th]], 0, 0, 1, r, g, bv);
      quad([[x0, y0, tz - th], [x1, y1, tz - th], [x1, y1, tz + th], [x0, y0, tz + th]], 0, 0, -1, r, g, bv);
      quad([[x0, y0, tz - th], [x0, y0, tz + th], [x1, y1, tz + th], [x1, y1, tz - th]], 0, 1, 0, r, g, bv);
    };
    tube(0, 0, 0, 4.8, 0.28, 16, 0.85, 0.87, 0.92);
    for (let i = 0; i < 16; i++) { const a0 = i / 16 * Math.PI * 2, a1 = (i + 1) / 16 * Math.PI * 2; push(3.1, 0, 0, 1, 0, 0, 0.9, 0.92, 0.95); push(2.4, Math.cos(a1) * 0.28, Math.sin(a1) * 0.28, Math.cos(a1), 0, Math.sin(a1), 0.88, 0.90, 0.94); push(2.4, Math.cos(a0) * 0.28, Math.sin(a0) * 0.28, Math.cos(a0), 0, Math.sin(a0), 0.88, 0.90, 0.94); }
    wing(-0.3, -2.5, 0, 1.5, 0, 0.75, 0.78, 0.86); wing(-0.3, -2.5, 0, -1.5, 0, 0.75, 0.78, 0.86);
    wing(-0.3, -2.5, -0.02, 1.5, 0, 0.55, 0.58, 0.70); wing(-0.3, -2.5, -0.02, -1.5, 0, 0.55, 0.58, 0.70);
    tube(-0.6, 0, 1.05, 1.0, 0.14, 12, 0.45, 0.47, 0.52); tube(-0.6, 0, -1.05, 1.0, 0.14, 12, 0.45, 0.47, 0.52);
    tube(-0.65, 0, 1.05, 1.0, 0.18, 12, 0.35, 0.37, 0.42); tube(-0.65, 0, -1.05, 1.0, 0.18, 12, 0.35, 0.37, 0.42);
    wing(-2.1, -2.7, 0, 0.7, 0, 0.75, 0.78, 0.86); wing(-2.1, -2.7, 0, -0.7, 0, 0.75, 0.78, 0.86);
    quad([[-2.1, 0, 0], [-2.7, 0.65, 0], [-2.7, 0, 0], [-2.7, 0, 0]], 0, 0, 1, 0.75, 0.78, 0.86);
    quad([[-2.1, 0, 0], [-2.7, 0.65, 0], [-2.7, 0.65, 0], [-2.1, 0, 0]], 1, 0, 0, 0.72, 0.75, 0.83);
    quad([[2.0, 0.22, 0.18], [2.3, 0.16, 0.14], [2.3, 0.05, 0.14], [2.0, 0.05, 0.18]], 0.5, 0.5, 0.5, 0.4, 0.55, 0.85);
    quad([[2.0, 0.22, -0.18], [2.3, 0.16, -0.14], [2.3, 0.05, -0.14], [2.0, 0.05, -0.18]], 0.5, 0.5, -0.5, 0.4, 0.55, 0.85);
    tube(0, 0.25, 0, 4.5, 0.005, 16, 0.08, 0.28, 0.72);
    return { verts: new Float32Array(verts), norms: new Float32Array(norms), cols: new Float32Array(cols), count: verts.length / 3 };
  }, []);

  const uploadGeo = useCallback((gl, geo) => {
    const upload = (data) => { const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b); gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW); return b; };
    return { vb: upload(geo.verts), nb: upload(geo.norms), cb: upload(geo.cols), count: geo.count };
  }, []);

  const mat4 = {
    perspective: (fov, asp, n, f) => { const t = Math.tan(fov / 2), m = new Float32Array(16); m[0] = 1 / (asp * t); m[5] = 1 / t; m[10] = -(f + n) / (f - n); m[11] = -1; m[14] = -2 * f * n / (f - n); return m; },
    rotX: (a) => { const c = Math.cos(a), s = Math.sin(a); return new Float32Array([1, 0, 0, 0, 0, c, s, 0, 0, -s, c, 0, 0, 0, 0, 1]); },
    rotY: (a) => { const c = Math.cos(a), s = Math.sin(a); return new Float32Array([c, 0, -s, 0, 0, 1, 0, 0, s, 0, c, 0, 0, 0, 0, 1]); },
    rotZ: (a) => { const c = Math.cos(a), s = Math.sin(a); return new Float32Array([c, s, 0, 0, -s, c, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]); },
    translate: (tx, ty, tz) => new Float32Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, tx, ty, tz, 1]),
    mul: (a, b) => { const m = new Float32Array(16); for (let r = 0; r < 4; r++) for (let c = 0; c < 4; c++) for (let k = 0; k < 4; k++) m[r + c * 4] += a[r + k * 4] * b[k + c * 4]; return m; }
  };

  useEffect(() => {
    const canvas = canvasRef.current;
    const gl = canvas.getContext("webgl");
    if (!gl) return;
    glRef.current = gl;
    gl.enable(gl.DEPTH_TEST);
    gl.enable(gl.CULL_FACE);
    programRef.current = buildProgram(gl);
    buffersRef.current = uploadGeo(gl, buildFallbackGeo());
    return () => cancelAnimationFrame(animRef.current);
  }, [buildProgram, buildFallbackGeo, uploadGeo]);

  useEffect(() => {
    const gl = glRef.current;
    if (!gl) return;
    buffersRef.current = uploadGeo(gl, modelData || buildFallbackGeo());
  }, [modelData, buildFallbackGeo, uploadGeo]);

  useEffect(() => {
    const gl = glRef.current;
    const prog = programRef.current;
    if (!gl || !prog) return;
    cancelAnimationFrame(animRef.current);
    const hasCritical = showAlerts && getAlerts(flightData).some(a => a.severity === "critical");
    const render = (now) => {
      const canvas = canvasRef.current;
      const bufs = buffersRef.current;
      if (!canvas || !bufs) { animRef.current = requestAnimationFrame(render); return; }
      const W = canvas.clientWidth * window.devicePixelRatio, H = canvas.clientHeight * window.devicePixelRatio;
      if (canvas.width !== W || canvas.height !== H) { canvas.width = W; canvas.height = H; }
      gl.viewport(0, 0, W, H);
      gl.clearColor(0.04, 0.06, 0.10, 1);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      gl.useProgram(prog);
      const baseRotation = mat4.rotY(Math.PI / 2);
      const attitude = mat4.mul(
        mat4.rotZ(-flightData.bankAngle * Math.PI / 180),
        mat4.rotX(-flightData.pitchAngle * Math.PI / 180)
      );
      const model = mat4.mul(baseRotation, attitude);
      const mvp = mat4.mul(mat4.perspective(0.9, W / H, 0.1, 50), mat4.mul(mat4.translate(0, -0.07, -4.5), model));
      gl.uniformMatrix4fv(gl.getUniformLocation(prog, "uMVP"),   false, mvp);
      gl.uniformMatrix4fv(gl.getUniformLocation(prog, "uModel"), false, model);
      gl.uniform3f(gl.getUniformLocation(prog, "uLight"), 2.0, 3.0, 2.0);
      gl.uniform1f(gl.getUniformLocation(prog, "uAlertPulse"), hasCritical ? (Math.sin(now * 0.008) * 0.5 + 0.5) : 0);
      const aPos = gl.getAttribLocation(prog, "aPos"), aNorm = gl.getAttribLocation(prog, "aNorm"), aColor = gl.getAttribLocation(prog, "aColor");
      gl.bindBuffer(gl.ARRAY_BUFFER, bufs.vb); gl.enableVertexAttribArray(aPos);  gl.vertexAttribPointer(aPos,  3, gl.FLOAT, false, 0, 0);
      gl.bindBuffer(gl.ARRAY_BUFFER, bufs.nb); gl.enableVertexAttribArray(aNorm); gl.vertexAttribPointer(aNorm, 3, gl.FLOAT, false, 0, 0);
      gl.bindBuffer(gl.ARRAY_BUFFER, bufs.cb); gl.enableVertexAttribArray(aColor);gl.vertexAttribPointer(aColor,3, gl.FLOAT, false, 0, 0);
      gl.drawArrays(gl.TRIANGLES, 0, bufs.count);
      animRef.current = requestAnimationFrame(render);
    };
    animRef.current = requestAnimationFrame(render);
    return () => cancelAnimationFrame(animRef.current);
  }, [flightData, showAlerts]);

  return <canvas ref={canvasRef} style={{ width: "100%", height: "100%", display: "block", borderRadius: 8 }} />;
}

// ─── Alert Overlay ────────────────────────────────────────────────────────────
function AlertOverlay({ alerts }) {
  if (!alerts.length) return null;
  const colors   = { critical: "#ff2020", warning: "#ff9900", advisory: "#ffdd00" };
  const bgColors = { critical: "rgba(255,20,20,0.12)", warning: "rgba(255,140,0,0.10)", advisory: "rgba(255,220,0,0.08)" };
  return (
    <div style={{ position: "absolute", top: 12, left: 12, right: 12, zIndex: 20, display: "flex", flexDirection: "column", gap: 6, pointerEvents: "none" }}>
      {alerts.map(a => (
        <div key={a.id} style={{
          background: bgColors[a.severity], border: `1px solid ${colors[a.severity]}`,
          borderRadius: 6, padding: "6px 12px",
          display: "flex", alignItems: "center", gap: 12,
          animation: a.severity === "critical" ? "alertPulse 0.6s ease-in-out infinite alternate" : "none",
          backdropFilter: "blur(4px)"
        }}>
          <div style={{ width: 8, height: 8, borderRadius: "50%", background: colors[a.severity], boxShadow: `0 0 8px ${colors[a.severity]}` }} />
          <div>
            <div style={{ color: colors[a.severity], fontFamily: "'Courier New',monospace", fontWeight: 700, fontSize: 13, letterSpacing: 2 }}>{a.msg}</div>
            <div style={{ color: colors[a.severity], opacity: 0.75, fontSize: 11, fontFamily: "'Courier New',monospace" }}>{a.detail}</div>
          </div>
          <div style={{ marginLeft: "auto", color: colors[a.severity], fontFamily: "monospace", fontSize: 10, opacity: 0.8 }}>{a.id}</div>
        </div>
      ))}
    </div>
  );
}

// ─── Role Selection Screen ────────────────────────────────────────────────────
function RoleSelector({ onSelect }) {
  const [selectedResponsibility, setSelectedResponsibility] = useState("pm");
  const roleResponsibilities = {
    pf: [
      "Controls aircraft",
      "Manages flight path",
      "Makes critical decisions during emergencies",
      "WILL NOT RECEIVE SYSTEM ALERTS - MUST FOCUS ON PILOTING AND RELY ON PM FOR ALERTS"
    ],
    pm: [
      "Monitors aircraft systems",
      "Communicates with air traffic control",
      "Manages alerts",
      "WILL RECEIVE ALL SYSTEM ALERTS - MUST PRIORITIZE ALERTS AND COMMUNICATE CRITICAL INFORMATION TO PF & AIR TRAFFIC CONTROL"
    ]
  };

  return (
    <div style={{
      minHeight: "100vh", background: "#050b14",
      display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
      fontFamily: "'Courier New', monospace",
      position: "fixed", overflow: "hidden",
      height: "100vh", width: "100vw", top: 0, left: 0,
      backgroundImage: "radial-gradient(ellipse at 50% 0%, rgba(0,60,120,0.3) 0%, transparent 70%)"
    }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Rajdhani:wght@400;500;600;700&family=Share+Tech+Mono&display=swap');
        @keyframes scanline { 0%{top:-8%} 100%{top:108%} }
        @keyframes alertPulse { from{opacity:1} to{opacity:0.5} }
        .role-card { transition: all 0.2s; cursor:pointer; }
        .role-card:hover { transform:translateY(-2px); }
      `}</style>
      <div style={{ position: "fixed", inset: 0, pointerEvents: "none", overflow: "hidden", zIndex: 100 }}>
        <div style={{ position: "absolute", width: "100%", height: "8%", background: "linear-gradient(transparent,rgba(0,180,255,0.03),transparent)", animation: "scanline 5s linear infinite" }} />
      </div>
      <div style={{ textAlign: "center", marginBottom: 48 }}>
        <div style={{ fontSize: 11, letterSpacing: 8, color: "#2060a0", marginBottom: 8, fontFamily: "'Share Tech Mono', monospace" }}>AERIS SYSTEMS · AERIS v4.2</div>
        <div style={{ fontSize: 32, fontWeight: 700, color: "#d0e8ff", letterSpacing: 4, fontFamily: "'Rajdhani', sans-serif", textTransform: "uppercase" }}>Aircraft Emergency Response</div>
        <div style={{ fontSize: 22, fontWeight: 500, color: "#4090d0", letterSpacing: 6, fontFamily: "'Rajdhani', sans-serif" }}>Intelligent System</div>
        <div style={{ width: 180, height: 1, background: "linear-gradient(90deg,transparent,#2060a0,transparent)", margin: "16px auto" }} />
        <div style={{ fontSize: 11, color: "#b7c1d4", letterSpacing: 3, fontFamily: "'Share Tech Mono', monospace" }}>PLEASE SELECT YOUR ROLE</div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 24, marginBottom: 32, width: 620 }}>
        {[
          { key: "pf", label: "PILOT FLYING",    abbr: "PF", desc: "Controls aircraft · Primary flight duties", color: "#0060cc", glow: "rgba(0,96,204,0.3)",   icon: "✈" },
          { key: "pm", label: "PILOT MONITORING", abbr: "PM", desc: "Monitors systems · Receives alerts",       color: "#cc6000", glow: "rgba(204,96,0,0.3)",  icon: "◉" }
        ].map(role => (
          <div key={role.key} className="role-card" style={{
            background: "#0a1628",
            border: `1px solid ${selectedResponsibility === role.key ? role.color : "#1a2a3a"}`,
            borderRadius: 12, padding: 20,
            boxShadow: selectedResponsibility === role.key ? `0 0 24px ${role.glow}` : ""
          }} onClick={() => setSelectedResponsibility(role.key)}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16 }}>
              <div style={{ fontSize: 20 }}>{role.icon}</div>
              <div>
                <div style={{ fontSize: 10, color: "#3060a0", letterSpacing: 3, fontFamily: "'Share Tech Mono',monospace" }}>{role.abbr}</div>
                <div style={{ fontSize: 14, fontWeight: 700, color: "#a0c0e0", letterSpacing: 2, fontFamily: "'Rajdhani',sans-serif" }}>{role.label}</div>
              </div>
            </div>
            <div style={{ fontSize: 10, color: "#3a5575", marginBottom: 14, fontFamily: "'Share Tech Mono',monospace" }}>{role.desc}</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {roleResponsibilities[role.key].map((resp, i) => (
                <div key={i} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <div style={{ width: 6, height: 6, borderRadius: "50%", background: role.color, flexShrink: 0 }} />
                  <div style={{ fontSize: 9, color: "#6080a0", fontFamily: "'Share Tech Mono',monospace" }}>{resp}</div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
      <button onClick={() => onSelect(selectedResponsibility)} disabled={!selectedResponsibility} style={{
        background: selectedResponsibility ? "linear-gradient(135deg,#0050aa,#0080cc)" : "#0d1a28",
        border: `1px solid ${selectedResponsibility ? "#0060bb" : "#1a2a3a"}`,
        borderRadius: 8, padding: "12px 48px", color: selectedResponsibility ? "#e0f0ff" : "#2a4060",
        fontFamily: "'Rajdhani',sans-serif", fontSize: 14, fontWeight: 700, letterSpacing: 4,
        cursor: selectedResponsibility ? "pointer" : "not-allowed", transition: "all 0.2s", textTransform: "uppercase"
      }}>Initialize Flight Deck</button>
      <div style={{ position: "absolute", bottom: 16, fontSize: 18, color: "#cad8e6", letterSpacing: 3, fontFamily: "'Rajdhani',sans-serif" }}>
        AVIATE · NAVIGATE · COMMUNICATE
      </div>
    </div>
  );
}

// ─── Main Flight Deck ─────────────────────────────────────────────────────────
function FlightDeck({ selectedResponsibility, onReset }) {
  const { data: fd, connected } = useJSBSimData();
  const alerts = getAlerts(fd);
  const [activeView, setActiveView] = useState("3d");
  const [modelData, setModelData] = useState(null);

  useEffect(() => {
    loadAircraftModel().then(setModelData).catch(() => {
      // null → AircraftViewer uses its own fallback geometry
    });
  }, []);

  return (
    <div style={{
      minHeight: "100vh", background: "#040a10",
      fontFamily: "'Share Tech Mono','Courier New',monospace",
      display: "flex", flexDirection: "column",
      backgroundImage: "radial-gradient(ellipse at 50% 0%,rgba(0,40,80,0.4) 0%,transparent 60%)",
      position: "fixed", overflow: "hidden",
      top: 0, left: 0, right: 0, bottom: 0
    }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Rajdhani:wght@400;500;600;700&family=Share+Tech+Mono&display=swap');
        @keyframes alertPulse { from{opacity:1;box-shadow:0 0 8px #ff202080} to{opacity:0.65;box-shadow:0 0 20px #ff202060} }
        @keyframes blink { 0%,100%{opacity:1} 50%{opacity:0.2} }
        ::-webkit-scrollbar{width:4px} ::-webkit-scrollbar-track{background:#05080f} ::-webkit-scrollbar-thumb{background:#1a3050}
      `}</style>

      {/* Top Bar */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "8px 18px", background: "#030811", borderBottom: "1px solid #0d1e30" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <div style={{ width: 8, height: 8, borderRadius: "50%", background: connected ? "#00cc55" : "#cc3333", boxShadow: connected ? "0 0 6px #00cc55" : "0 0 6px #cc3333" }} />
          <span style={{ fontSize: 10, color: "#2060a0", letterSpacing: 3 }}>AERIS · AERS v4.2</span>
          <span style={{ fontSize: 10, color: "#1a3050" }}>|</span>
          <span style={{ fontSize: 10, color: "#204060", letterSpacing: 2 }}>FLT AXB-441 · {new Date().toUTCString().slice(17, 22)}Z</span>
          <span style={{ fontSize: 10, color: connected ? "#005522" : "#551111", letterSpacing: 2 }}>
            {connected ? "● AERIS ENGINE CONNECTED" : "○ AERIS ENGINE OFFLINE"}
          </span>
        </div>
        <div style={{ display: "flex", gap: 20, alignItems: "center" }}>
          <div style={{ textAlign: "center" }}>
            <div style={{ fontSize: 8, color: "#204060", letterSpacing: 2 }}>PILOT FLYING</div>
            <div style={{ fontSize: 10, color: "#60aadd", letterSpacing: 1 }}>{selectedResponsibility === "pf" ? "ON" : "OFF"}</div>
          </div>
          <div style={{ width: 1, height: 24, background: "#0d1e30" }} />
          <div style={{ textAlign: "center" }}>
            <div style={{ fontSize: 8, color: "#204060", letterSpacing: 2 }}>MONITORING</div>
            <div style={{ fontSize: 10, color: "#dd9040", letterSpacing: 1 }}>{selectedResponsibility === "pm" ? "ON" : "OFF"}</div>
          </div>
          {alerts.length > 0 && (
            <div style={{ background: "rgba(255,30,30,0.1)", border: "1px solid #ff3030", borderRadius: 4, padding: "3px 10px", animation: "alertPulse 0.7s ease-in-out infinite alternate" }}>
              <span style={{ color: "#ff4040", fontSize: 10, letterSpacing: 2 }}>{alerts.length} ALERT{alerts.length > 1 ? "S" : ""}</span>
            </div>
          )}
          <button onClick={onReset} style={{ background: "none", border: "1px solid #1a2a3a", borderRadius: 4, color: "#2a4060", padding: "4px 10px", cursor: "pointer", fontSize: 9, letterSpacing: 2 }}>← CREW</button>
        </div>
      </div>

      {/* Main layout */}
      <div style={{ display: "flex", flex: 1, overflow: "hidden" }}>

        {/* CENTER — main content */}
        <div style={{ position: "relative", background: "#04090f", display: "flex", flexDirection: "column", flex: 1, minWidth: 0 }}>
          {/* Sub-nav */}
          <div style={{ display: "flex", gap: 1, padding: "8px 12px", background: "#030810", borderBottom: "1px solid #0d1e30" }}>
            {["3d", "checklist"].map(v => (
              <button key={v} onClick={() => setActiveView(v)} style={{
                background: activeView === v ? "#0d1e30" : "none", border: "1px solid",
                borderColor: activeView === v ? "#1a3050" : "transparent",
                borderRadius: 4, padding: "4px 14px", color: activeView === v ? "#60aadd" : "#2a4060",
                cursor: "pointer", fontSize: 9, letterSpacing: 2, textTransform: "uppercase"
              }}>{v}</button>
            ))}
            <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 12 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
                <div style={{ fontSize: 9, color: "#1a3050" }}>AERIS</div>
                <div style={{ width: 6, height: 6, borderRadius: "50%", background: connected ? "#00cc55" : "#cc3333", animation: connected ? "blink 2s ease-in-out infinite" : "none" }} />
              </div>
            </div>
          </div>

          {/* 3D View */}
          {activeView === "3d" && (
            <div style={{ flex: 1, position: "relative", minHeight: 300 }}>
              <AircraftViewer flightData={fd} showAlerts={true} modelData={modelData} />
              <AlertOverlay alerts={alerts} />
              {/* HUD overlay — bottom left */}
              <div style={{ position: "absolute", bottom: 12, left: 12, display: "flex", flexDirection: "column", gap: 4, pointerEvents: "none" }}>
                <div style={{ fontSize: 9, color: "#2a5070", fontFamily: "monospace", letterSpacing: 1 }}>
                  {fd.phase.replace("_", " ")} · V1 {fd.v1.toFixed(0)} VR {fd.vr.toFixed(0)} V2 {fd.v2.toFixed(0)} kts
                </div>
                <div style={{ fontSize: 9, color: "#1a3555", fontFamily: "monospace" }}>
                  BANK {fd.bankAngle.toFixed(1)}° · PITCH {fd.pitchAngle.toFixed(1)}° · HDG {(((fd.headingAngle % 360) + 360) % 360).toFixed(1)}°
                </div>
                <div style={{ fontSize: 9, color: "#1a3555", fontFamily: "monospace" }}>
                  ALT {Math.round(fd.altitude)} ft · IAS {Math.round(fd.airspeed)} kts · GS {Math.round(fd.groundspeed)} kts
                </div>
                {fd.anyDisagree && (
                  <div style={{ fontSize: 8, color: "#ff9900", fontFamily: "monospace", letterSpacing: 1 }}>
                    ⚠ SENSOR DISAGREE — CROSS-CHECK INSTRUMENTS
                  </div>
                )}
              </div>
              {/* Status — bottom right */}
              <div style={{ position: "absolute", bottom: 12, right: 12, pointerEvents: "none" }}>
                <div style={{ fontSize: 8, color: "#1a3050", fontFamily: "monospace", letterSpacing: 2, textAlign: "right" }}>
                  {fd.captainSuspect && <div style={{ color: "#ff4444" }}>CAPT SUSPECT</div>}
                  {fd.foSuspect      && <div style={{ color: "#ff4444" }}>F/O SUSPECT</div>}
                  {alerts.length === 0
                    ? <span style={{ color: "#0a5030" }}>NO ACTIVE ALERTS</span>
                    : <span style={{ color: "#502010", animation: "blink 1s infinite" }}>MONITORING PILOT ALERT ACTIVE</span>}
                </div>
              </div>
            </div>
          )}

          {/* Checklist View */}
          {activeView === "checklist" && (
            <div style={{ flex: 1, padding: 20, overflowY: "auto" }}>
              <div style={{ fontSize: 10, color: "#2060a0", letterSpacing: 3, marginBottom: 16 }}>EMERGENCY REFERENCE · QRH</div>
              {[
                { title: "ENGINE FAILURE IN FLIGHT",   steps: ["Thrust lever — IDLE", "Dead engine — identify", "Engine Master switch — OFF", "Squawk 7700", "Declare emergency", "Nearest suitable airport — divert"] },
                { title: "STALL RECOVERY",              steps: ["Autopilot — DISCONNECT", "Pitch — NOSE DOWN", "Bank — WINGS LEVEL", "Thrust — INCREASE", "Airspeed — MONITOR min Vstall+20"] },
                { title: "UNUSUAL ATTITUDE RECOVERY",   steps: ["TRUST YOUR INSTRUMENTS — ignore body sensations", "Identify attitude on ADI", "If nose-high: reduce pitch, add power, wings level", "If nose-low: wings level FIRST, then pull to horizon", "Return to level flight smoothly"] },
                { title: "GPWS BANK ANGLE",             steps: ["Roll wings level immediately", "Autopilot — check/disconnect if not responding", "Heading — hold", "EGPWS alert — acknowledge", "Report to ATC"] },
                { title: "SPATIAL DISORIENTATION",      steps: ["STOP — do not follow body sensations", "TRUST THE INSTRUMENTS", "Make small, deliberate control inputs", "If unsure: straight and level — wings level, pitch neutral", "Request ATC assistance if needed"] },
              ].map(c => (
                <div key={c.title} style={{ background: "#06101a", border: "1px solid #0d1e30", borderRadius: 8, padding: 14, marginBottom: 10 }}>
                  <div style={{ fontSize: 10, color: "#60aadd", letterSpacing: 2, marginBottom: 10, fontFamily: "'Rajdhani',sans-serif", fontWeight: 700 }}>{c.title}</div>
                  {c.steps.map((s, i) => (
                    <div key={i} style={{ display: "flex", gap: 10, padding: "4px 0", borderBottom: "1px solid #0a1520" }}>
                      <span style={{ color: "#1a4060", fontSize: 9, minWidth: 16 }}>{String(i + 1).padStart(2, "0")}</span>
                      <span style={{ color: "#4a7090", fontSize: 10 }}>{s}</span>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* RIGHT — Attitude Reference Panel (always visible) */}
        <AttitudeReferencePanel fd={fd} connected={connected} />
      </div>
    </div>
  );
}

// ─── App Root ─────────────────────────────────────────────────────────────────
export default function App() {
  const [selectedResponsibility, setSelectedResponsibility] = useState(null);
  if (!selectedResponsibility) return <RoleSelector onSelect={setSelectedResponsibility} />;
  return <FlightDeck selectedResponsibility={selectedResponsibility} onReset={() => setSelectedResponsibility(null)} />;
}
