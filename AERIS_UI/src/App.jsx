import { useState, useEffect, useRef, useCallback } from "react";
import "./App.css";

import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import * as THREE from "three";

function loadAircraftModel() {
  return new Promise((resolve, reject) => {
    const loader = new GLTFLoader();

    loader.load(
      "/models/a330/Master.gltf", // ✅ from public folder
      (gltf) => {
        const geo = extractGeometry(gltf.scene);
        resolve(geo);
      },
      undefined,
      reject
    );
  });
}

function extractGeometry(scene) {
  const verts = [];
  const norms = [];
  const cols = [];

  // 1. Force the scene to calculate all part positions relative to each other
  scene.updateMatrixWorld(true);

  const box = new THREE.Box3().setFromObject(scene);
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  const maxDim = Math.max(size.x, size.y, size.z);
  const SCALE = 4 / maxDim;

  scene.traverse((node) => {
    if (!node.isMesh) return;

    // 2. Convert indexed geometry to "flat" geometry for gl.drawArrays
    let geometry = node.geometry;
    if (geometry.index) {
      geometry = geometry.toNonIndexed();
    }

    const position = geometry.attributes.position;
    const normal = geometry.attributes.normal;
    const color = new THREE.Color(0.8, 0.8, 0.85);

    // Temp vectors for transformation
    const v = new THREE.Vector3();
    const n = new THREE.Vector3();
    
    // 3. Get the transformation matrix for THIS specific part
    const matrix = node.matrixWorld;

    for (let i = 0; i < position.count; i++) {
      // Apply the part's world position/rotation to the vertex
      v.fromBufferAttribute(position, i);
      v.applyMatrix4(matrix);

      verts.push(
        (v.x - center.x) * SCALE,
        (v.y - center.y) * SCALE,
        (v.z - center.z) * SCALE
      );

      if (normal) {
        // Normals only need rotation, not translation
        n.fromBufferAttribute(normal, i);
        n.transformDirection(matrix);
        norms.push(n.x, n.y, n.z);
      } else {
        norms.push(0, 1, 0);
      }

      cols.push(color.r, color.g, color.b);
    }
    
    // Clean up the temporary non-indexed geometry
    if (node.geometry.index) geometry.dispose();
  });

  return {
    verts: new Float32Array(verts),
    norms: new Float32Array(norms),
    cols: new Float32Array(cols),
    count: verts.length / 3,
  };
}
// ─── Simulated JSBSim-style flight data stream ───────────────────────────────
function useFlightData() {
  const [data, setData] = useState({
    bankAngle: 0,
    pitchAngle: 2,
    headingAngle: 45,
    altitude: 35000,
    verticalSpeed: 0,
    airspeed: 480,
    engineN1L: 88,
    engineN1R: 88,
    engineN2L: 92,
    engineN2R: 92,
    fuelLeft: 18200,
    fuelRight: 18050,
    hydraulicA: 3000,
    hydraulicB: 3000,
    apu: false,
    gearDown: false,
    flapAngle: 0,
    spoilers: false,
    autoThrottle: true,
    autopilot: true,
    pitotHeat: true,
    stall: false,
    overspeed: false,
    gpws: false,
    tcas: false,
  });
  const t = useRef(0);

  useEffect(() => {
    const id = setInterval(() => {
      t.current += 0.05;
      setData(prev => {
        const bank = Math.sin(t.current * 0.3) * 18 + Math.sin(t.current * 0.7) * 6;
        const pitch = Math.sin(t.current * 0.2) * 4 + 2;
        const vs = Math.sin(t.current * 0.25) * 120;
        const spd = 480 + Math.sin(t.current * 0.4) * 8;
        const n1L = 88 + Math.sin(t.current * 0.15) * 2;
        const n1R = 88 + Math.sin(t.current * 0.18 + 0.5) * 2;
        // Simulate occasional alert conditions
        const stall = spd < 478 && Math.abs(pitch) > 5.5;
        const overspeed = spd > 490;
        const gpws = Math.abs(bank) > 14;
        const tcas = t.current > 12 && t.current < 14;
        return {
          ...prev,
          bankAngle: bank,
          pitchAngle: pitch,
          headingAngle: (prev.headingAngle + 0.02) % 360,
          altitude: 35000 + Math.sin(t.current * 0.1) * 80,
          verticalSpeed: vs,
          airspeed: spd,
          engineN1L: n1L,
          engineN1R: n1R,
          fuelLeft: Math.max(0, prev.fuelLeft - 0.3),
          fuelRight: Math.max(0, prev.fuelRight - 0.28),
          stall,
          overspeed,
          gpws,
          tcas,
        };
      });
    }, 80);
    return () => clearInterval(id);
  }, []);

  return data;
}

// ─── Active Alerts ────────────────────────────────────────────────────────────
function getAlerts(fd) {
  const alerts = [];
  if (fd.stall) alerts.push({ id: "STALL", severity: "critical", msg: "STALL WARNING", detail: "Increase speed immediately" });
  if (fd.overspeed) alerts.push({ id: "OVSPD", severity: "critical", msg: "OVERSPEED", detail: "Reduce thrust — VMO exceeded" });
  if (fd.gpws) alerts.push({ id: "GPWS", severity: "warning", msg: "BANK ANGLE", detail: "Excessive bank — reduce roll" });
  if (fd.tcas) alerts.push({ id: "TCAS", severity: "advisory", msg: "TCAS RA", detail: "Traffic advisory — climb" });
  return alerts;
}


// ─── 3D Aircraft WebGL Renderer ──────────────────────────────────────────────
function AircraftViewer({ flightData, showAlerts, modelData }) {
  const canvasRef = useRef(null);
  const glRef = useRef(null);
  const programRef = useRef(null);
  const buffersRef = useRef(null);
  const animRef = useRef(null);

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
    mul: (a, b) => { const m = new Float32Array(16); for (let r = 0; r < 4; r++)for (let c = 0; c < 4; c++)for (let k = 0; k < 4; k++)m[r + c * 4] += a[r + k * 4] * b[k + c * 4]; return m; }
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
    const geo = modelData || buildFallbackGeo();
    buffersRef.current = uploadGeo(gl, geo);
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
const baseRotation = mat4.rotY(Math.PI / 2); // 👉 face right

const attitude = mat4.mul(
  mat4.rotZ(-flightData.bankAngle * Math.PI / 180), // roll
  mat4.rotX(flightData.pitchAngle * Math.PI / 180)  // pitch
);

const model = mat4.mul(baseRotation, attitude);
      const mvp = mat4.mul(mat4.perspective(0.9, W / H, 0.1, 50), mat4.mul(mat4.translate(0, -0.2, -6), model));
      gl.uniformMatrix4fv(gl.getUniformLocation(prog, "uMVP"), false, mvp);
      gl.uniformMatrix4fv(gl.getUniformLocation(prog, "uModel"), false, model);
      gl.uniform3f(gl.getUniformLocation(prog, "uLight"), 2.0, 3.0, 2.0);
      gl.uniform1f(gl.getUniformLocation(prog, "uAlertPulse"), hasCritical ? (Math.sin(now * 0.008) * 0.5 + 0.5) : 0);
      const aPos = gl.getAttribLocation(prog, "aPos"), aNorm = gl.getAttribLocation(prog, "aNorm"), aColor = gl.getAttribLocation(prog, "aColor");
      gl.bindBuffer(gl.ARRAY_BUFFER, bufs.vb); gl.enableVertexAttribArray(aPos); gl.vertexAttribPointer(aPos, 3, gl.FLOAT, false, 0, 0);
      gl.bindBuffer(gl.ARRAY_BUFFER, bufs.nb); gl.enableVertexAttribArray(aNorm); gl.vertexAttribPointer(aNorm, 3, gl.FLOAT, false, 0, 0);
      gl.bindBuffer(gl.ARRAY_BUFFER, bufs.cb); gl.enableVertexAttribArray(aColor); gl.vertexAttribPointer(aColor, 3, gl.FLOAT, false, 0, 0);
      gl.drawArrays(gl.TRIANGLES, 0, bufs.count);
      animRef.current = requestAnimationFrame(render);
    };
    animRef.current = requestAnimationFrame(render);
    return () => cancelAnimationFrame(animRef.current);
  }, [flightData, showAlerts]);

  return <canvas ref={canvasRef} style={{ width: "100%", height: "100%", display: "block", borderRadius: 8 }} />;
}


// ─── Alert Overlay (Monitoring pilot only) ───────────────────────────────────
function AlertOverlay({ alerts }) {
  if (!alerts.length) return null;
  const colors = { critical: "#ff2020", warning: "#ff9900", advisory: "#ffdd00" };
  const bgColors = { critical: "rgba(255,20,20,0.12)", warning: "rgba(255,140,0,0.10)", advisory: "rgba(255,220,0,0.08)" };
  return (
    <div style={{ position: "absolute", top: 12, left: 12, right: 12, zIndex: 20, display: "flex", flexDirection: "column", gap: 6, pointerEvents: "none" }}>
      {alerts.map(a => (
        <div key={a.id} style={{
          background: bgColors[a.severity],
          border: `1px solid ${colors[a.severity]}`,
          borderRadius: 6, padding: "6px 12px",
          display: "flex", alignItems: "center", gap: 12,
          animation: a.severity === "critical" ? "alertPulse 0.6s ease-in-out infinite alternate" : "none",
          backdropFilter: "blur(4px)"
        }}>
          <div style={{ width: 8, height: 8, borderRadius: "50%", background: colors[a.severity], boxShadow: `0 0 8px ${colors[a.severity]}` }} />
          <div>
            <div style={{ color: colors[a.severity], fontFamily: "'Courier New', monospace", fontWeight: 700, fontSize: 13, letterSpacing: 2 }}>{a.msg}</div>
            <div style={{ color: colors[a.severity], opacity: 0.75, fontSize: 11, fontFamily: "'Courier New', monospace" }}>{a.detail}</div>
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
  const [roleResponsibilities, setRoleResponsibilities] = useState({
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
  });

  const [flyingMantra, setFlyingMantra] = useState("AVIATE · NAVIGATE · COMMUNICATE");

 const handleRoleSelect = (role) => {
    setSelectedResponsibility(role);
  }
const handleConfirm = () => {
    onSelect(selectedResponsibility);
  }
  return (
    <div style={{
      minHeight: "100vh", background: "#050b14",
      display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
      fontFamily: "'Courier New', monospace",
      position: "fixed", overflow: "hidden",
      height: "100vh",
      width: "100vw",
      top: 0, left: 0,
      backgroundImage: "radial-gradient(ellipse at 50% 0%, rgba(0,60,120,0.3) 0%, transparent 70%)"
    }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Rajdhani:wght@400;500;600;700&family=Share+Tech+Mono&display=swap');
        @keyframes scanline { 0%{top:-8%} 100%{top:108%} }
        @keyframes flicker { 0%,100%{opacity:1} 95%{opacity:0.97} 96%{opacity:0.92} }
        @keyframes blink { 0%,100%{opacity:1} 50%{opacity:0.3} }
        @keyframes alertPulse { from{opacity:1} to{opacity:0.5} }
        .role-card { transition: all 0.2s; cursor:pointer; }
        .role-card:hover { transform:translateY(-2px); }
        .pilot-btn { transition:all 0.15s; cursor:pointer; }
        .pilot-btn:hover { filter:brightness(1.15); }
      `}</style>

      {/* Scanline effect */}
      <div style={{ position: "fixed", inset: 0, pointerEvents: "none", overflow: "hidden", zIndex: 100 }}>
        <div style={{ position: "absolute", width: "100%", height: "8%", background: "linear-gradient(transparent,rgba(0,180,255,0.03),transparent)", animation: "scanline 5s linear infinite" }} />
      </div>

      {/* Logo / Header */}
      <div style={{ textAlign: "center", marginBottom: 48 }}>
        <div style={{ fontSize: 11, letterSpacing: 8, color: "#2060a0", marginBottom: 8, fontFamily: "'Share Tech Mono', monospace" }}>AERIS SYSTEMS · AERS v4.2</div>
        <div style={{ fontSize: 32, fontWeight: 700, color: "#d0e8ff", letterSpacing: 4, fontFamily: "'Rajdhani', sans-serif", textTransform: "uppercase" }}>
          Aircraft Emergency Response
        </div>
        <div style={{ fontSize: 22, fontWeight: 500, color: "#4090d0", letterSpacing: 6, fontFamily: "'Rajdhani', sans-serif" }}>
          Intelligent System
        </div>
        <div style={{ width: 180, height: 1, background: "linear-gradient(90deg,transparent,#2060a0,transparent)", margin: "16px auto" }} />
        <div style={{ fontSize: 11, color: "#b7c1d4", letterSpacing: 3, fontFamily: "'Share Tech Mono', monospace" }}>PLEASE SELECT YOUR ROLE</div>
      </div>

      {/* Role Cards */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 24, marginBottom: 32, width: 620 }}>
        {[
          { key: "pf", label: "PILOT FLYING", abbr: "PF", desc: "Controls aircraft · Primary flight duties", color: "#0060cc", glow: "rgba(0,96,204,0.3)", icon: "✈" },
          { key: "pm", label: "PILOT MONITORING", abbr: "PM", desc: "Monitors systems · Receives alerts", color: "#cc6000", glow: "rgba(204,96,0,0.3)", icon: "◉" }
        ].map(role => (
          <div key={role.key} className="role-card" style={{
            background: "#0a1628", border: `1px solid  ${selectedResponsibility === role.key ? "#" + role.color.slice(1) : "#1a2a3a"}`,
            borderRadius: 12, padding: 20, boxShadow: selectedResponsibility === role.key ? `0 0 24px ${role.glow}` : ""
          }} onClick={() => handleRoleSelect(role.key)}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16 }}>
              <div style={{ fontSize: 20 }}>{role.icon}</div>
              <div>
                <div style={{ fontSize: 10, color: "#3060a0", letterSpacing: 3, fontFamily: "'Share Tech Mono', monospace" }}>{role.abbr}</div>
                <div style={{ fontSize: 14, fontWeight: 700, color: "#a0c0e0", letterSpacing: 2, fontFamily: "'Rajdhani', sans-serif" }}>{role.label}</div>
              </div>
            </div>
            <div style={{ fontSize: 10, color: "#3a5575", marginBottom: 14, fontFamily: "'Share Tech Mono', monospace" }}>{role.desc}</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {
                roleResponsibilities[role.key].map((resp, i) => (
                  <div key={i} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <div style={{ width: 6, height: 6, borderRadius: "50%", background: role.color }} />
                    <div style={{ fontSize: 9, color: "#6080a0", fontFamily: "'Share Tech Mono', monospace" }}>{resp}</div>
                  </div>
                ))
              }

            </div>
          </div>
        ))}
      </div>

      {/* Confirm */}
      <button onClick={handleConfirm} disabled={!selectedResponsibility} style={{
        background: selectedResponsibility ? "linear-gradient(135deg,#0050aa,#0080cc)" : "#0d1a28",
        border: `1px solid ${selectedResponsibility ? "#0060bb" : "#1a2a3a"}`,
        borderRadius: 8, padding: "12px 48px", color: selectedResponsibility ? "#e0f0ff" : "#2a4060",
        fontFamily: "'Rajdhani', sans-serif", fontSize: 14, fontWeight: 700, letterSpacing: 4, cursor: selectedResponsibility ? "pointer" : "not-allowed",
        transition: "all 0.2s", textTransform: "uppercase"
      }}>Initialize Flight Deck</button>

{/*      Flying Mantra */}
      <div style={{ position: "absolute", bottom: 16, fontSize: 18,  color: "#cad8e6", letterSpacing: 3, fontFamily: "'Rajdhani', sans-serif" }}>{flyingMantra}</div>
    </div>
  );
}

// ─── Main Flight Deck ─────────────────────────────────────────────────────────
function FlightDeck({ selectedResponsibility, onReset }) {
  const fd = useFlightData();
  const alerts = getAlerts(fd);
  const [activeView, setActiveView] = useState("3d");
  const [modelData, setModelData] = useState(null);
  const [modelName, setModelName] = useState(null);
  const handleModelLoad = (geo, name) => { setModelData(geo); setModelName(name); };

  const FMT = (v, dec = 0) => v.toFixed(dec);
  const col = (v, warn, crit) => v > crit ? "#ff3333" : v > warn ? "#ffaa00" : "#40e0a0";

  const DataRow = ({ label, value, unit, warn, crit }) => (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", padding: "3px 0", borderBottom: "1px solid #0d1a25" }}>
      <span style={{ color: "#3a5575", fontSize: 10, fontFamily: "'Share Tech Mono',monospace", letterSpacing: 1 }}>{label}</span>
      <span style={{ fontFamily: "'Share Tech Mono',monospace", fontSize: 12, fontWeight: 600, color: warn ? col(Math.abs(fd[value]), warn, crit) : "#70aadd" }}>
        {typeof fd[value] === "number" ? FMT(fd[value], value.includes("Angle") ? 1 : 0) : fd[value] ? "ON" : "OFF"}
        {unit && <span style={{ fontSize: 9, color: "#3a5575", marginLeft: 3 }}>{unit}</span>}
      </span>
    </div>
  );
useEffect(() => {
  loadAircraftModel().then((geo) => {
    setModelData(geo);
  }).catch(() => {
    setModelData(buildFallbackGeo());
  });
}, []);
  return (
    <div style={{
      minHeight: "100vh", background: "#040a10",

      fontFamily: "'Share Tech Mono', 'Courier New', monospace",
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
          <div style={{ width: 8, height: 8, borderRadius: "50%", background: "#00cc55", boxShadow: "0 0 6px #00cc55" }} />
          <span style={{ fontSize: 10, color: "#2060a0", letterSpacing: 3 }}>AERIS · AERS v4.2</span>
          <span style={{ fontSize: 10, color: "#1a3050" }}>|</span>
          <span style={{ fontSize: 10, color: "#204060", letterSpacing: 2 }}>FLT AXB-441 · {new Date().toUTCString().slice(17, 22)}Z</span>
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


        {/* CENTER — 3D Viewer */}
        <div style={{ position: "relative", background: "#04090f", display: "flex", flexDirection: "column", width: "100%" }}>
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
                <div style={{ fontSize: 9, color: "#1a3050" }}>JSBSim</div>
                <div style={{ width: 6, height: 6, borderRadius: "50%", background: "#00cc55", animation: "blink 2s ease-in-out infinite" }} />
              </div>
            </div>
          </div>

          {/* 3D View */}
          {activeView === "3d" && (
            <div style={{ flex: 1, position: "relative", minHeight: 300 }}>
              <AircraftViewer flightData={fd} showAlerts={true} modelData={modelData} />
              {/* Monitoring pilot's alert overlay — shown over 3D */}
              <AlertOverlay alerts={alerts} />
              {/* HUD overlay labels */}
              <div style={{ position: "absolute", bottom: 12, left: 12, display: "flex", flexDirection: "column", gap: 4, pointerEvents: "none" }}>
                <div style={{ fontSize: 9, color: "#1a3555", fontFamily: "monospace" }}>BANK {fd.bankAngle.toFixed(1)}° · PITCH {fd.pitchAngle.toFixed(1)}°</div>
                <div style={{ fontSize: 9, color: "#1a3555", fontFamily: "monospace" }}>ALT {Math.round(fd.altitude)} ft · IAS {Math.round(fd.airspeed)} kts</div>
              </div>
              <div style={{ position: "absolute", bottom: 12, right: 12, pointerEvents: "none" }}>
                <div style={{ fontSize: 8, color: "#1a3050", fontFamily: "monospace", letterSpacing: 2, textAlign: "right" }}>
                  {alerts.length === 0 ? <span style={{ color: "#0a5030" }}>NO ACTIVE ALERTS</span> : <span style={{ color: "#502010", animation: "blink 1s infinite" }}>MONITORING PILOT ALERT ACTIVE</span>}
                </div>
              </div>
            </div>
          )}

          {/* Instruments View */}
          {activeView === "instruments" && (
            <div style={{ flex: 1, padding: 24, display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 16, overflowY: "auto" }}>
              {[
                { label: "AIRSPEED", val: fd.airspeed, unit: "kts", min: 150, max: 600, warn: 485, crit: 495, normal: 480 },
                { label: "ALTITUDE", val: fd.altitude, unit: "ft", min: 0, max: 45000, warn: 36000, crit: 40000, normal: 35000 },
                { label: "V/SPEED", val: fd.verticalSpeed, unit: "fpm", min: -2000, max: 2000, warn: 800, crit: 1500, normal: 0 },
                { label: "ENG 1 N1", val: fd.engineN1L, unit: "%", min: 0, max: 110, warn: 91, crit: 96, normal: 88 },
                { label: "ENG 2 N1", val: fd.engineN1R, unit: "%", min: 0, max: 110, warn: 91, crit: 96, normal: 88 },
                { label: "BANK", val: Math.abs(fd.bankAngle), unit: "°", min: 0, max: 90, warn: 15, crit: 30, normal: 0 },
              ].map(g => {
                const pct = (g.val - g.min) / (g.max - g.min) * 100;
                const vc = g.val > g.crit ? "#ff3333" : g.val > g.warn ? "#ffaa00" : "#40e0a0";
                return (
                  <div key={g.label} style={{ background: "#07111c", border: "1px solid #0d1e30", borderRadius: 8, padding: 14 }}>
                    <div style={{ fontSize: 8, color: "#2a4060", letterSpacing: 3, marginBottom: 10 }}>{g.label}</div>
                    <div style={{ fontSize: 24, fontWeight: 700, color: vc, fontFamily: "'Share Tech Mono',monospace", marginBottom: 8 }}>
                      {g.val.toFixed(g.label === "ALTITUDE" ? 0 : 1)}<span style={{ fontSize: 10, color: "#2a4060", marginLeft: 4 }}>{g.unit}</span>
                    </div>
                    <div style={{ height: 4, background: "#0d1a25", borderRadius: 2 }}>
                      <div style={{ height: "100%", width: `${Math.min(100, Math.max(0, pct))}%`, background: vc, borderRadius: 2, transition: "width 0.2s" }} />
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {/* Checklist View */}
          {activeView === "checklist" && (
            <div style={{ flex: 1, padding: 20, overflowY: "auto" }}>
              <div style={{ fontSize: 10, color: "#2060a0", letterSpacing: 3, marginBottom: 16 }}>EMERGENCY REFERENCE · QRH</div>
              {[
                { title: "ENGINE FAILURE IN FLIGHT", steps: ["Thrust lever — IDLE", "Dead engine — identify", "Engine Master switch — OFF", "Squawk 7700", "Declare emergency", "Nearest suitable airport — divert"] },
                { title: "STALL RECOVERY", steps: ["Autopilot — DISCONNECT", "Pitch — NOSE DOWN", "Bank — WINGS LEVEL", "Thrust — INCREASE", "Airspeed — MONITOR min Vstall+20"] },
                { title: "GPWS BANK ANGLE", steps: ["Roll wings level immediately", "Autopilot — check/disconnect if not responding", "Heading — hold", "EGPWS alert — acknowledge", "Report to ATC"] },
                { title: "HYDRAULIC SYSTEM FAILURE", steps: ["HYD amber light — check", "Verify system A or B", "Electric pump — select ON", "Alternate braking — available", "Gear extension — alternate"] },
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