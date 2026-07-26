import { useEffect, useRef, useState, useCallback, useMemo } from "react";

// ─── Projection ─────────────────────────────────────────────────────────────
// Flat equirectangular chart projection (not a globe) — x-scale corrected by
// cos(refLat) around a fixed CONUS reference point. Accurate enough for a
// stylized ATC/sectional-style chart; not a precision nav projection.
const DEG2RAD = Math.PI / 180;
const REF_LAT = 39.5;   // geographic-ish center of the contiguous US
const REF_LON = -98.5;
const COS_REF_LAT = Math.cos(REF_LAT * DEG2RAD);

const CONUS_STATES = new Set([
  "AL","AZ","AR","CA","CO","CT","DE","DC","FL","GA","ID","IL","IN","IA","KS",
  "KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM",
  "NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT","VA",
  "WA","WV","WI","WY",
]);

function project(lat, lon) {
  return {
    x: (lon - REF_LON) * COS_REF_LAT,
    y: REF_LAT - lat,
  };
}

const TYPE_COLOR = {
  large_airport:  "#40e0ff",
  medium_airport: "#ffaa00",
  small_airport:  "#3a5d80",
};
const TYPE_RADIUS = {
  large_airport:  4.5,
  medium_airport: 3,
  small_airport:  1.6,
};

const MIN_ZOOM = 0.08;
const MAX_ZOOM = 4000;
const LOD_ALL_DOTS   = 60;    // effScale: below this only large/medium shown
const LOD_ELEV_LABEL = 250;   // effScale: show boxed elevation + ICAO
const LOD_RUNWAYS    = 1500;  // effScale: show true-scale runway lines

function useAirports() {
  const [airports, setAirports] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetch("/data/airports.json")
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((raw) => {
        const withProj = raw.map((a) => {
          const p = project(a.lat, a.lon);
          return {
            ...a,
            _x: p.x,
            _y: p.y,
            _runwaysProj: (a.runways || []).map((rwy) => ({
              ...rwy,
              _ends: rwy.ends.map((e) => ({ ...e, ...project(e.lat, e.lon) })),
            })),
          };
        });
        setAirports(withProj);
      })
      .catch((e) => setError(e.message));
  }, []);

  return { airports, error };
}

// ─── Live flight (WS) ───────────────────────────────────────────────────────
// Radar-relevant fields only — not the full cockpit/ADC parsing App.jsx does.
const WS_URL = "ws://localhost:8765";

function useLiveFlight() {
  const [connected, setConnected] = useState(false);
  const [meta, setMeta] = useState(null);
  const [flight, setFlight] = useState(null);
  const wsRef = useRef(null);
  const timerRef = useRef(null);

  useEffect(() => {
    function connect() {
      try { wsRef.current?.close(); } catch (_) {}
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onopen = () => { setConnected(true); clearTimeout(timerRef.current); };
      ws.onclose = () => {
        setConnected(false);
        timerRef.current = setTimeout(connect, 2000);
      };
      ws.onerror = () => ws.close();

      ws.onmessage = ({ data: raw }) => {
        try {
          const s = JSON.parse(raw);
          if (s.topic === "flight_meta") {
            setMeta(s);
            setFlight(null);
            return;
          }
          if (s.topic) return; // alerts etc. — not relevant to the radar view
          if (s.lat == null || s.lon == null) return;
          setFlight({
            lat: s.lat,
            lon: s.lon,
            track_deg: s.track_deg ?? 0,
            pressure_alt_ft: s.pressure_alt_ft ?? 0,
            groundspeed_kts: s.groundspeed_kts ?? 0,
            phase: s.phase ?? "",
            time: s.time ?? 0,
          });
        } catch (_) {}
      };
    }

    connect();
    return () => { clearTimeout(timerRef.current); wsRef.current?.close(); };
  }, []);

  return { connected, meta, flight };
}

function computeBounds(airports, predicate) {
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const a of airports) {
    if (predicate && !predicate(a)) continue;
    if (a._x < minX) minX = a._x;
    if (a._x > maxX) maxX = a._x;
    if (a._y < minY) minY = a._y;
    if (a._y > maxY) maxY = a._y;
  }
  if (!isFinite(minX)) return computeBounds(airports, null);
  return { minX, maxX, minY, maxY, cx: (minX + maxX) / 2, cy: (minY + maxY) / 2 };
}

// ─── Screen <-> world transform helpers ────────────────────────────────────
function worldToScreen(x, y, view, base, w, h) {
  return {
    sx: (x - base.center.x) * base.scale * view.zoom + w / 2 + view.panX,
    sy: (y - base.center.y) * base.scale * view.zoom + h / 2 + view.panY,
  };
}
function screenToWorld(sx, sy, view, base, w, h) {
  return {
    x: (sx - w / 2 - view.panX) / (base.scale * view.zoom) + base.center.x,
    y: (sy - h / 2 - view.panY) / (base.scale * view.zoom) + base.center.y,
  };
}

// ─── Main screen ────────────────────────────────────────────────────────────
export default function SimulateMap() {
  const { airports, error } = useAirports();
  const { connected, meta, flight } = useLiveFlight();
  const canvasRef = useRef(null);
  const containerRef = useRef(null);

  const viewRef = useRef({ zoom: 1, panX: 0, panY: 0 });
  const baseRef = useRef(null); // { scale, center:{x,y}, fullBounds }
  const dragRef = useRef(null); // { startX, startY, startPanX, startPanY, moved }
  const animRef = useRef(null);
  const trailRef = useRef([]);   // sampled {x,y,t} world-coord history
  const followRef = useRef(false);
  const firstFixRef = useRef(false);

  const originAirport = useMemo(
    () => (airports && meta ? airports.find((a) => a.icao === meta.origin_icao) : null),
    [airports, meta]
  );
  const destAirport = useMemo(
    () => (airports && meta ? airports.find((a) => a.icao === meta.destination_icao) : null),
    [airports, meta]
  );

  const [hovered, setHovered] = useState(null);
  const [selected, setSelected] = useState(null);
  const [query, setQuery] = useState("");
  const [notFound, setNotFound] = useState(false);

  const getCanvasSize = () => {
    const c = canvasRef.current;
    return { w: c.clientWidth, h: c.clientHeight };
  };

  // ── Draw ──────────────────────────────────────────────────────────────
  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas || !airports || !baseRef.current) return;
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const { w, h } = getCanvasSize();
    if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
      canvas.width = w * dpr;
      canvas.height = h * dpr;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const view = viewRef.current;
    const base = baseRef.current;
    const effScale = base.scale * view.zoom;

    // Background
    ctx.fillStyle = "#040a10";
    ctx.fillRect(0, 0, w, h);

    // Graticule (every 10 degrees of lon/lat, faint)
    ctx.strokeStyle = "rgba(30,60,90,0.35)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    const step = 10;
    const topLeft = screenToWorld(0, 0, view, base, w, h);
    const botRight = screenToWorld(w, h, view, base, w, h);
    const lonStart = Math.floor((topLeft.x / COS_REF_LAT + REF_LON) / step) * step;
    const lonEnd = Math.ceil((botRight.x / COS_REF_LAT + REF_LON) / step) * step;
    for (let lon = lonStart; lon <= lonEnd; lon += step) {
      const wx = (lon - REF_LON) * COS_REF_LAT;
      const { sx } = worldToScreen(wx, 0, view, base, w, h);
      ctx.moveTo(sx, 0);
      ctx.lineTo(sx, h);
    }
    const latStart = Math.floor((REF_LAT - botRight.y) / step) * step;
    const latEnd = Math.ceil((REF_LAT - topLeft.y) / step) * step;
    for (let lat = latStart; lat <= latEnd; lat += step) {
      const wy = REF_LAT - lat;
      const { sy } = worldToScreen(0, wy, view, base, w, h);
      ctx.moveTo(0, sy);
      ctx.lineTo(w, sy);
    }
    ctx.stroke();

    // Flight-plan route (origin -> destination), if a flight is live
    if (originAirport && destAirport) {
      const p0 = worldToScreen(originAirport._x, originAirport._y, view, base, w, h);
      const p1 = worldToScreen(destAirport._x, destAirport._y, view, base, w, h);
      ctx.save();
      ctx.setLineDash([6, 6]);
      ctx.strokeStyle = "rgba(255,180,60,0.5)";
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(p0.sx, p0.sy);
      ctx.lineTo(p1.sx, p1.sy);
      ctx.stroke();
      ctx.restore();
    }

    const showAllDots = effScale >= LOD_ALL_DOTS;
    const showElevLabel = effScale >= LOD_ELEV_LABEL;
    const showRunways = effScale >= LOD_RUNWAYS;

    let visibleCount = 0;

    for (const a of airports) {
      if (!showAllDots && a.airport_type === "small_airport") continue;
      const { sx, sy } = worldToScreen(a._x, a._y, view, base, w, h);
      if (sx < -40 || sx > w + 40 || sy < -40 || sy > h + 40) continue;
      visibleCount++;

      const isSel = selected && selected.icao === a.icao;
      const isHov = hovered && hovered.icao === a.icao;

      // Runway lines (true-scale, true-heading — projected from real endpoints)
      if (showRunways && a._runwaysProj.length) {
        ctx.strokeStyle = isSel ? "#ffffff" : "rgba(120,200,255,0.85)";
        ctx.lineWidth = isSel ? 2.5 : 1.5;
        for (const rwy of a._runwaysProj) {
          if (rwy._ends.length < 2) continue;
          const p0 = worldToScreen(rwy._ends[0].x, rwy._ends[0].y, view, base, w, h);
          const p1 = worldToScreen(rwy._ends[1].x, rwy._ends[1].y, view, base, w, h);
          ctx.beginPath();
          ctx.moveTo(p0.sx, p0.sy);
          ctx.lineTo(p1.sx, p1.sy);
          ctx.stroke();
          if (effScale >= LOD_RUNWAYS * 1.6) {
            ctx.fillStyle = "rgba(150,210,255,0.9)";
            ctx.font = "10px 'Share Tech Mono', monospace";
            ctx.fillText(rwy._ends[0].id, p0.sx + 4, p0.sy - 4);
            ctx.fillText(rwy._ends[1].id, p1.sx + 4, p1.sy - 4);
          }
        }
      }

      // Marker
      const color = TYPE_COLOR[a.airport_type];
      const r = TYPE_RADIUS[a.airport_type] * (isSel ? 1.8 : isHov ? 1.5 : 1);
      if (isSel || isHov) {
        ctx.beginPath();
        ctx.arc(sx, sy, r + 6, 0, Math.PI * 2);
        ctx.strokeStyle = isSel ? "#ffffff" : "#60aadd";
        ctx.lineWidth = 1.2;
        ctx.stroke();
      }
      ctx.beginPath();
      ctx.arc(sx, sy, r, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.shadowColor = color;
      ctx.shadowBlur = isSel || isHov ? 10 : 4;
      ctx.fill();
      ctx.shadowBlur = 0;

      // Elevation label (sectional-chart style boxed number)
      if (showElevLabel) {
        const label = `${Math.round(a.elev_ft)}`;
        ctx.font = "9px 'Share Tech Mono', monospace";
        const tw = ctx.measureText(label).width;
        ctx.fillStyle = "rgba(4,10,16,0.75)";
        ctx.fillRect(sx + r + 3, sy - 9, tw + 6, 12);
        ctx.strokeStyle = "rgba(96,170,221,0.6)";
        ctx.lineWidth = 1;
        ctx.strokeRect(sx + r + 3, sy - 9, tw + 6, 12);
        ctx.fillStyle = "#a0d0ff";
        ctx.fillText(label, sx + r + 6, sy);

        if (effScale >= LOD_RUNWAYS) {
          ctx.fillStyle = "rgba(200,220,240,0.85)";
          ctx.font = "9px 'Share Tech Mono', monospace";
          ctx.fillText(a.icao, sx - 4, sy - r - 6);
        }
      }
    }

    // ── Live flight overlay ────────────────────────────────────────────
    if (flight) {
      const trail = trailRef.current;
      for (let i = 0; i < trail.length; i++) {
        const pt = trail[i];
        const { sx: tx, sy: ty } = worldToScreen(pt.x, pt.y, view, base, w, h);
        const alpha = 0.12 + 0.4 * (i / Math.max(1, trail.length - 1));
        ctx.beginPath();
        ctx.arc(tx, ty, 2, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(255,200,80,${alpha})`;
        ctx.fill();
      }

      const acWorld = project(flight.lat, flight.lon);
      const { sx: ax, sy: ay } = worldToScreen(acWorld.x, acWorld.y, view, base, w, h);

      // Target symbol — chevron pointing along ground track (like a real radar
      // return, not the instrument/nose heading)
      ctx.save();
      ctx.translate(ax, ay);
      ctx.rotate(flight.track_deg * DEG2RAD);
      ctx.beginPath();
      ctx.moveTo(0, -9);
      ctx.lineTo(6, 7);
      ctx.lineTo(0, 3);
      ctx.lineTo(-6, 7);
      ctx.closePath();
      ctx.fillStyle = "#ffcc33";
      ctx.shadowColor = "#ffcc33";
      ctx.shadowBlur = 12;
      ctx.fill();
      ctx.restore();
      ctx.shadowBlur = 0;

      // Leader line + data block (ATC-style)
      const dbX = ax + 26, dbY = ay - 26;
      ctx.strokeStyle = "rgba(255,204,51,0.7)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(ax + 5, ay - 5);
      ctx.lineTo(dbX, dbY);
      ctx.stroke();

      const lines = [
        meta?.model ?? "AIRCRAFT",
        `${Math.round(flight.pressure_alt_ft)} FT`,
        `${Math.round(flight.groundspeed_kts)} KT`,
        flight.phase,
      ];
      ctx.font = "10px 'Share Tech Mono', monospace";
      const boxW = Math.max(...lines.map((l) => ctx.measureText(l).width)) + 10;
      ctx.fillStyle = "rgba(4,10,16,0.8)";
      ctx.fillRect(dbX, dbY - 10, boxW, lines.length * 12 + 6);
      ctx.strokeStyle = "rgba(255,204,51,0.6)";
      ctx.strokeRect(dbX, dbY - 10, boxW, lines.length * 12 + 6);
      ctx.fillStyle = "#ffd766";
      lines.forEach((l, i) => ctx.fillText(l, dbX + 5, dbY + 2 + i * 12));
    }

    // HUD readout (drawn on canvas corner too, so it stays with the chart)
    ctx.fillStyle = "rgba(42,64,96,0.9)";
    ctx.font = "9px 'Share Tech Mono', monospace";
    ctx.fillText(`RENDERED ${visibleCount} / ${airports.length}`, 10, h - 10);
  }, [airports, hovered, selected, flight, meta, originAirport, destAirport]);

  // ── Init base transform once airports load ─────────────────────────────
  useEffect(() => {
    if (!airports) return;
    const { w, h } = getCanvasSize();
    const conus = computeBounds(airports, (a) => CONUS_STATES.has(a.state));
    const pad = 0.92;
    const scale = Math.min(
      (w * pad) / (conus.maxX - conus.minX),
      (h * pad) / (conus.maxY - conus.minY)
    );
    baseRef.current = { scale, center: { x: conus.cx, y: conus.cy }, conus };
    viewRef.current = { zoom: 1, panX: 0, panY: 0 };
    draw();
  }, [airports]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    draw();
  }, [draw]);

  // ── Resize handling ─────────────────────────────────────────────────────
  useEffect(() => {
    const ro = new ResizeObserver(() => {
      if (baseRef.current && airports) {
        const { w, h } = getCanvasSize();
        const conus = baseRef.current.conus;
        const pad = 0.92;
        baseRef.current.scale = Math.min(
          (w * pad) / (conus.maxX - conus.minX),
          (h * pad) / (conus.maxY - conus.minY)
        );
      }
      draw();
    });
    if (containerRef.current) ro.observe(containerRef.current);
    return () => ro.disconnect();
  }, [airports, draw]);

  // ── Live flight: trail sampling, follow-camera, redraw ──────────────────
  const recenterOn = useCallback((lat, lon) => {
    if (!baseRef.current) return;
    const base = baseRef.current;
    const view = viewRef.current;
    const p = project(lat, lon);
    viewRef.current = {
      ...view,
      panX: -(p.x - base.center.x) * base.scale * view.zoom,
      panY: -(p.y - base.center.y) * base.scale * view.zoom,
    };
  }, []);

  useEffect(() => {
    trailRef.current = [];
    firstFixRef.current = false;
  }, [meta]);

  useEffect(() => {
    if (!flight) return;
    const trail = trailRef.current;
    const lastPt = trail[trail.length - 1];
    if (!lastPt || flight.time - lastPt.t >= 10) {
      const p = project(flight.lat, flight.lon);
      trail.push({ x: p.x, y: p.y, t: flight.time });
      if (trail.length > 60) trail.shift();
    }

    if (!firstFixRef.current) {
      firstFixRef.current = true;
      followRef.current = true;
    }
    if (followRef.current) recenterOn(flight.lat, flight.lon);

    draw();
  }, [flight, recenterOn, draw]);

  const trackAircraft = useCallback(() => {
    followRef.current = true;
    if (flight) recenterOn(flight.lat, flight.lon);
    draw();
  }, [flight, recenterOn, draw]);

  // ── Pointer interaction ─────────────────────────────────────────────────
  const hitTest = useCallback(
    (sx, sy) => {
      if (!airports || !baseRef.current) return null;
      const { w, h } = getCanvasSize();
      const view = viewRef.current;
      const base = baseRef.current;
      const effScale = base.scale * view.zoom;
      const showAllDots = effScale >= LOD_ALL_DOTS;
      let best = null, bestD = 12 * 12; // 12px hit radius
      for (const a of airports) {
        if (!showAllDots && a.airport_type === "small_airport") continue;
        const { sx: x, sy: y } = worldToScreen(a._x, a._y, view, base, w, h);
        const d = (x - sx) ** 2 + (y - sy) ** 2;
        if (d < bestD) { bestD = d; best = a; }
      }
      return best;
    },
    [airports]
  );

  const onWheel = useCallback(
    (e) => {
      e.preventDefault();
      if (!baseRef.current) return;
      followRef.current = false;
      const canvas = canvasRef.current;
      const rect = canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      const { w, h } = getCanvasSize();
      const view = viewRef.current;
      const base = baseRef.current;

      const before = screenToWorld(mx, my, view, base, w, h);
      const factor = Math.exp(-e.deltaY * 0.0015);
      const newZoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, view.zoom * factor));
      const newPanX = mx - w / 2 - (before.x - base.center.x) * base.scale * newZoom;
      const newPanY = my - h / 2 - (before.y - base.center.y) * base.scale * newZoom;
      viewRef.current = { zoom: newZoom, panX: newPanX, panY: newPanY };
      draw();
    },
    [draw]
  );

  const onMouseDown = useCallback((e) => {
    dragRef.current = {
      startX: e.clientX,
      startY: e.clientY,
      startPanX: viewRef.current.panX,
      startPanY: viewRef.current.panY,
      moved: false,
    };
  }, []);

  const onMouseMove = useCallback(
    (e) => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      const rect = canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;

      if (dragRef.current) {
        const dx = e.clientX - dragRef.current.startX;
        const dy = e.clientY - dragRef.current.startY;
        if (Math.abs(dx) > 3 || Math.abs(dy) > 3) {
          dragRef.current.moved = true;
          followRef.current = false;
        }
        viewRef.current = {
          ...viewRef.current,
          panX: dragRef.current.startPanX + dx,
          panY: dragRef.current.startPanY + dy,
        };
        draw();
        return;
      }

      const hit = hitTest(mx, my);
      setHovered((prev) => (prev?.icao === hit?.icao ? prev : hit));
    },
    [hitTest, draw]
  );

  const onMouseUp = useCallback(
    (e) => {
      const canvas = canvasRef.current;
      const wasDrag = dragRef.current?.moved;
      dragRef.current = null;
      if (!wasDrag && canvas) {
        const rect = canvas.getBoundingClientRect();
        const hit = hitTest(e.clientX - rect.left, e.clientY - rect.top);
        setSelected(hit);
      }
    },
    [hitTest]
  );

  useEffect(() => {
    draw();
  }, [hovered, selected, draw]);

  // ── Fly-to (search) ──────────────────────────────────────────────────────
  const flyTo = useCallback(
    (airport) => {
      if (!baseRef.current || !canvasRef.current) return;
      const { w, h } = getCanvasSize();
      const base = baseRef.current;
      const start = { ...viewRef.current };
      const targetZoom = Math.max(start.zoom, 400);
      const targetPanX = -(airport._x - base.center.x) * base.scale * targetZoom;
      const targetPanY = -(airport._y - base.center.y) * base.scale * targetZoom;
      const target = { zoom: targetZoom, panX: targetPanX, panY: targetPanY };

      cancelAnimationFrame(animRef.current);
      const t0 = performance.now();
      const dur = 650;
      const step = (now) => {
        const t = Math.min(1, (now - t0) / dur);
        const e = 1 - Math.pow(1 - t, 3); // ease-out cubic
        viewRef.current = {
          zoom: start.zoom + (target.zoom - start.zoom) * e,
          panX: start.panX + (target.panX - start.panX) * e,
          panY: start.panY + (target.panY - start.panY) * e,
        };
        draw();
        if (t < 1) animRef.current = requestAnimationFrame(step);
      };
      animRef.current = requestAnimationFrame(step);
      setSelected(airport);
    },
    [draw]
  );

  const onSearch = useCallback(
    (e) => {
      e.preventDefault();
      if (!airports) return;
      const q = query.trim().toUpperCase();
      if (!q) return;
      const found = airports.find((a) => a.icao === q || a.iata === q);
      if (found) {
        setNotFound(false);
        flyTo(found);
      } else {
        setNotFound(true);
      }
    },
    [airports, query, flyTo]
  );

  const resetView = useCallback(() => {
    viewRef.current = { zoom: 1, panX: 0, panY: 0 };
    setSelected(null);
    draw();
  }, [draw]);

  // ── Styles ───────────────────────────────────────────────────────────────
  const panelBg = "#06101a";
  const border = "1px solid #0d1e30";
  const label = { fontSize: 8, color: "#2a4060", letterSpacing: 2, fontFamily: "'Share Tech Mono',monospace" };

  return (
    <div style={{
      position: "fixed", inset: 0, background: "#040a10",
      fontFamily: "'Share Tech Mono','Courier New',monospace",
      display: "flex", flexDirection: "column", overflow: "hidden",
    }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Rajdhani:wght@400;500;600;700&family=Share+Tech+Mono&display=swap');
        .aeris-map-sweep {
          position: absolute; inset: 0; pointer-events: none; z-index: 5;
          background: conic-gradient(from 0deg, rgba(96,170,221,0.10), transparent 22%);
          animation: aeris-sweep 6s linear infinite;
          mix-blend-mode: screen;
        }
        @keyframes aeris-sweep { from { transform: rotate(0deg);} to { transform: rotate(360deg);} }
        .aeris-map-input::placeholder { color: #1a3050; }
      `}</style>

      {/* Top Bar */}
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "8px 18px", background: "#030811", borderBottom: border, zIndex: 10,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <a href="/" style={{ color: "#2a4060", textDecoration: "none", fontSize: 9, letterSpacing: 2, border: "1px solid #1a2a3a", borderRadius: 4, padding: "4px 10px" }}>← BACK</a>
          <span style={{ fontSize: 10, color: "#2060a0", letterSpacing: 3 }}>AERIS · SIMULATE MAP</span>
          <span style={{ fontSize: 10, color: "#1a3050" }}>|</span>
          <span style={{ fontSize: 10, color: "#204060", letterSpacing: 2 }}>
            {airports ? `${airports.length} AIRPORTS LOADED` : "LOADING…"}
          </span>
          <span style={{ fontSize: 10, color: "#1a3050" }}>|</span>
          <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
            <div style={{ width: 6, height: 6, borderRadius: "50%", background: connected ? "#00cc55" : "#cc3333", boxShadow: connected ? "0 0 5px #00cc55" : "none" }} />
            <span style={{ fontSize: 9, color: connected ? "#00aa44" : "#aa2222", letterSpacing: 2 }}>
              {connected ? "AERIS ENGINE CONNECTED" : "ENGINE OFFLINE"}
            </span>
          </div>
          {meta && (
            <span style={{ fontSize: 10, color: "#dd9040", letterSpacing: 1 }}>
              {meta.model} · {meta.origin_icao} → {meta.destination_icao}
            </span>
          )}
        </div>
        <form onSubmit={onSearch} style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {flight && (
            <button type="button" onClick={trackAircraft} style={{
              background: "#241a0d", border: "1px solid #4a3010", borderRadius: 4,
              color: "#ffcc33", padding: "5px 12px", fontSize: 9, letterSpacing: 2, cursor: "pointer",
            }}>◎ TRACK AIRCRAFT</button>
          )}
          <input
            className="aeris-map-input"
            value={query}
            onChange={(e) => { setQuery(e.target.value); setNotFound(false); }}
            placeholder="ICAO / IATA e.g. KJFK"
            style={{
              background: "#0a1522", border: `1px solid ${notFound ? "#aa3333" : "#1a3050"}`,
              borderRadius: 4, color: "#a0d0ff", padding: "5px 10px", fontSize: 11,
              letterSpacing: 1, width: 160, fontFamily: "'Share Tech Mono',monospace",
            }}
          />
          <button type="submit" style={{
            background: "#0d1e30", border: "1px solid #1a3050", borderRadius: 4,
            color: "#60aadd", padding: "5px 12px", fontSize: 9, letterSpacing: 2, cursor: "pointer",
          }}>LOCATE</button>
          <button type="button" onClick={resetView} style={{
            background: "none", border: "1px solid #1a2a3a", borderRadius: 4,
            color: "#2a4060", padding: "5px 12px", fontSize: 9, letterSpacing: 2, cursor: "pointer",
          }}>RESET VIEW</button>
        </form>
      </div>

      {/* Map area */}
      <div ref={containerRef} style={{ position: "relative", flex: 1, minHeight: 0 }}>
        {error && (
          <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", color: "#cc3333", fontSize: 12, letterSpacing: 2 }}>
            FAILED TO LOAD AIRPORT DATA — {error}
          </div>
        )}
        <canvas
          ref={canvasRef}
          style={{ width: "100%", height: "100%", display: "block", cursor: "grab" }}
          onWheel={onWheel}
          onMouseDown={onMouseDown}
          onMouseMove={onMouseMove}
          onMouseUp={onMouseUp}
          onMouseLeave={() => { dragRef.current = null; setHovered(null); }}
        />
        <div className="aeris-map-sweep" />

        {/* Hover tooltip */}
        {hovered && (!selected || selected.icao !== hovered.icao) && (
          <div style={{
            position: "absolute", left: 12, top: 12, background: "rgba(6,16,26,0.85)",
            border, borderRadius: 6, padding: "6px 10px", pointerEvents: "none", zIndex: 8,
          }}>
            <div style={{ color: "#60aadd", fontSize: 11, letterSpacing: 1 }}>{hovered.icao} · {hovered.name}</div>
            <div style={{ color: "#3a5575", fontSize: 9 }}>{hovered.city}, {hovered.state} · ELEV {Math.round(hovered.elev_ft)} ft</div>
          </div>
        )}

        {/* Legend */}
        <div style={{
          position: "absolute", left: 12, bottom: 12, background: "rgba(6,16,26,0.85)",
          border, borderRadius: 6, padding: "10px 12px", zIndex: 8, width: 190,
        }}>
          <div style={{ ...label, marginBottom: 6 }}>LEGEND</div>
          {[
            ["large_airport", "MAJOR / PART 139"],
            ["medium_airport", "REGIONAL / JET"],
            ["small_airport", "GENERAL AVIATION"],
          ].map(([type, desc]) => (
            <div key={type} style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
              <div style={{ width: 8, height: 8, borderRadius: "50%", background: TYPE_COLOR[type], boxShadow: `0 0 4px ${TYPE_COLOR[type]}` }} />
              <span style={{ fontSize: 9, color: "#4a7090" }}>{desc}</span>
            </div>
          ))}
          <div style={{ fontSize: 8, color: "#1a3050", marginTop: 6, lineHeight: 1.5 }}>
            SCROLL TO ZOOM · DRAG TO PAN<br />
            ELEVATION LABELS &amp; RUNWAYS<br />REVEAL AT HIGHER ZOOM
          </div>
        </div>

        {/* Selected airport info panel */}
        {selected && (
          <div style={{
            position: "absolute", right: 0, top: 0, bottom: 0, width: 260,
            background: "#030a14", borderLeft: border, padding: "14px 14px 10px",
            overflowY: "auto", zIndex: 9,
          }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 10 }}>
              <div>
                <div style={{ fontSize: 16, color: "#e0f0ff", fontWeight: 700, fontFamily: "'Rajdhani',sans-serif", letterSpacing: 1 }}>{selected.icao}</div>
                <div style={{ fontSize: 9, color: "#3a6090", letterSpacing: 1 }}>{selected.iata !== selected.icao ? selected.iata : ""}</div>
              </div>
              <button onClick={() => setSelected(null)} style={{ background: "none", border: "none", color: "#2a4060", cursor: "pointer", fontSize: 14 }}>✕</button>
            </div>
            <div style={{ fontSize: 11, color: "#a0c0e0", marginBottom: 2 }}>{selected.name}</div>
            <div style={{ fontSize: 9, color: "#3a5575", marginBottom: 10 }}>{selected.city}, {selected.state}</div>

            <div style={{ display: "flex", gap: 14, marginBottom: 12 }}>
              <div>
                <div style={label}>ELEVATION</div>
                <div style={{ fontSize: 14, color: "#60aadd" }}>{Math.round(selected.elev_ft)} ft</div>
              </div>
              <div>
                <div style={label}>CLASS</div>
                <div style={{ fontSize: 11, color: TYPE_COLOR[selected.airport_type] }}>
                  {selected.airport_type.replace("_airport", "").toUpperCase()}
                </div>
              </div>
            </div>

            <div style={{ ...label, marginBottom: 6, borderTop: border, paddingTop: 8 }}>
              RUNWAYS ({selected.runways.length})
            </div>
            {selected.runways.length === 0 && (
              <div style={{ fontSize: 9, color: "#2a4060" }}>NO RUNWAY DATA</div>
            )}
            {selected.runways.map((rwy) => (
              <div key={rwy.id} style={{ border, borderRadius: 4, padding: "6px 8px", marginBottom: 6 }}>
                <div style={{ fontSize: 11, color: "#a0d0ff", marginBottom: 3 }}>{rwy.id}</div>
                <div style={{ fontSize: 9, color: "#4a7090" }}>{rwy.length_ft} × {rwy.width_ft} ft · {rwy.surface || "UNK"}</div>
                {rwy.ends.map((e) => (
                  <div key={e.id} style={{ fontSize: 9, color: "#3a5575" }}>
                    RWY {e.id} — HDG {e.heading_true?.toFixed(0) ?? "—"}° · ELEV {e.elev_ft != null ? Math.round(e.elev_ft) : "—"} ft
                  </div>
                ))}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
