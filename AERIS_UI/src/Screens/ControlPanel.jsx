import { useMemo, useState } from "react";
import useEngineSocket from "../useEngineSocket";

const C = {
  bg: "#070d14",
  panel: "#0c1520",
  panelHi: "#10202f",
  line: "#1a3048",
  lineHi: "#26496b",
  text: "#c8dcec",
  dim: "#5f7b93",
  accent: "#40e0a0",
  warn: "#ffaa00",
  crit: "#ff3d3d",
  live: "#ffcc00",
};

const MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";

const SEV = { critical: C.crit, warning: C.warn, advisory: C.live };

function Card({ title, right, children, style }) {
  return (
    <section style={{
      background: C.panel, border: `1px solid ${C.line}`, borderRadius: 6,
      marginBottom: 12, ...style,
    }}>
      <header style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "8px 12px", borderBottom: `1px solid ${C.line}`,
        fontSize: 11, letterSpacing: 1.4, textTransform: "uppercase",
        color: C.dim, fontFamily: MONO,
      }}>
        <span>{title}</span>
        {right}
      </header>
      <div style={{ padding: 12 }}>{children}</div>
    </section>
  );
}

function Button({ children, onClick, tone = "normal", disabled, style }) {
  const tones = {
    normal: { bg: C.panelHi, border: C.lineHi, fg: C.text },
    go: { bg: "#123a2c", border: C.accent, fg: C.accent },
    stop: { bg: "#3a1414", border: C.crit, fg: "#ff8b8b" },
  };
  const t = tones[tone] || tones.normal;
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        background: disabled ? "#0e1620" : t.bg,
        border: `1px solid ${disabled ? C.line : t.border}`,
        color: disabled ? C.dim : t.fg,
        borderRadius: 4, padding: "6px 14px", fontSize: 12,
        fontFamily: MONO, letterSpacing: 0.6,
        cursor: disabled ? "not-allowed" : "pointer",
        ...style,
      }}
    >
      {children}
    </button>
  );
}

function Slider({ spec, value, onChange }) {
  const decimals = Number(spec.step) < 1 ? 2 : 0;
  return (
    <label style={{ display: "block", marginBottom: 10 }}>
      <div style={{
        display: "flex", justifyContent: "space-between",
        fontSize: 11, color: C.dim, fontFamily: MONO, marginBottom: 3,
      }}>
        <span>{spec.label}</span>
        <span style={{ color: C.text }}>
          {Number(value).toFixed(decimals)} <span style={{ color: C.dim }}>{spec.unit}</span>
        </span>
      </div>
      <input
        type="range"
        min={spec.min} max={spec.max} step={spec.step} value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        style={{ width: "100%", accentColor: C.accent }}
      />
    </label>
  );
}

const selectStyle = {
  width: "100%", background: C.bg, color: C.text,
  border: `1px solid ${C.line}`, borderRadius: 4,
  padding: "5px 7px", fontSize: 12, fontFamily: MONO,
};

function Choice({ spec, value, onChange, labels }) {
  // 128 channels in one flat list is unusable, so anything carrying group
  // information gets rendered as optgroups.
  const groups = spec.groups;
  return (
    <label style={{ display: "block", marginBottom: 10 }}>
      <div style={{ fontSize: 11, color: C.dim, fontFamily: MONO, marginBottom: 3 }}>
        {spec.label}
      </div>
      <select value={value} onChange={(e) => onChange(e.target.value)} style={selectStyle}>
        {groups
          ? Object.entries(groups).map(([name, items]) => (
              <optgroup key={name} label={name}>
                {items.map((c) => (
                  <option key={c.key} value={c.key}>{c.label}</option>
                ))}
              </optgroup>
            ))
          : spec.choices.map((c) => (
              <option key={c} value={c}>{labels?.[c] || c}</option>
            ))}
      </select>
    </label>
  );
}

function InjectorCard({ spec, active, params, setParam, onInject, onStop, labels, note }) {
  return (
    <div style={{
      background: active ? "#11241c" : C.panelHi,
      border: `1px solid ${active ? C.accent : C.line}`,
      borderRadius: 5, padding: 12,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <strong style={{ fontSize: 13, color: active ? C.accent : C.text }}>
          {spec.label}
        </strong>
        {active && (
          <span style={{
            fontSize: 10, fontFamily: MONO, color: C.accent,
            border: `1px solid ${C.accent}`, borderRadius: 3, padding: "1px 6px",
          }}>
            ACTIVE
          </span>
        )}
      </div>
      <p style={{ fontSize: 11, color: C.dim, margin: "4px 0 12px", lineHeight: 1.45 }}>
        {spec.blurb}
      </p>

      {spec.params.filter((p) => !p.hidden).map((p) =>
        p.type === "range" ? (
          <Slider key={p.name} spec={p} value={params[p.name]}
                  onChange={(v) => setParam(p.name, v)} />
        ) : (
          <Choice key={p.name} spec={p} value={params[p.name]}
                  labels={labels} onChange={(v) => setParam(p.name, v)} />
        )
      )}

      {note && (
        <p style={{ fontSize: 10.5, color: C.dim, margin: "0 0 10px", lineHeight: 1.45 }}>
          {note}
        </p>
      )}

      <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
        <Button tone="go" onClick={onInject} style={{ flex: 1 }}>
          {spec.multi ? "Add fault" : active ? "Update" : "Inject"}
        </Button>
        {active && (
          <Button tone="stop" onClick={onStop}>
            {spec.multi ? "Clear all" : "Stop"}
          </Button>
        )}
      </div>
    </div>
  );
}

function Readout({ label, value, unit, tone }) {
  return (
    <div style={{ minWidth: 92 }}>
      <div style={{ fontSize: 10, color: C.dim, fontFamily: MONO, letterSpacing: 1 }}>
        {label}
      </div>
      <div style={{ fontSize: 19, fontFamily: MONO, color: tone || C.text }}>
        {value}
        {unit && <span style={{ fontSize: 11, color: C.dim, marginLeft: 3 }}>{unit}</span>}
      </div>
    </div>
  );
}

export default function ControlPanel() {
  const { connected, frame, meta, manifest, status, alerts, log, send } = useEngineSocket();

  // Only what the user has actually moved lives here; anything untouched falls
  // back to the catalog default, so the sliders need no seeding pass.
  const [edits, setEdits] = useState({});
  const [source, setSource] = useState("xplane");
  const [pickedFlight, setPickedFlight] = useState("");

  const injectors = useMemo(() => manifest?.injectors || [], [manifest]);
  const flights = useMemo(() => manifest?.flights || [], [manifest]);
  const channels = useMemo(() => manifest?.channels || [], [manifest]);

  const channelByKey = useMemo(() => {
    const out = {};
    channels.forEach((c) => { out[c.key] = c; });
    return out;
  }, [channels]);

  const channelLabels = useMemo(() => {
    const out = {};
    (manifest?.noise_modes || []).forEach((m) => { out[m.key] = m.label; });
    (manifest?.noise_scales || []).forEach((s) => { out[s.key] = s.label; });
    return out;
  }, [manifest]);

  const params = useMemo(() => {
    const out = {};
    injectors.forEach((spec) => {
      out[spec.key] = Object.fromEntries(spec.params.map((p) => [
        p.name, edits[spec.key]?.[p.name] ?? p.default,
      ]));
    });
    // Whether a magnitude reads better as a percentage or in the channel's own
    // units depends on the channel, so follow its recommendation until the
    // user overrides it themselves.
    const ch = channelByKey[out.noise?.channel];
    if (ch) {
      if (edits.noise?.scale === undefined) out.noise.scale = ch.default_scale;
      // A magnitude carried over from the last channel can be far outside the
      // new one's range — 20 makes sense for airspeed and is enormous for
      // Mach. The slider would show it pinned to the end while still sending
      // the old number, so clamp here, where what is displayed is what is sent.
      const [lo, hi] = out.noise.scale === "relative" ? [-200, 200] : ch.span;
      const clamp = (v) => Math.min(hi, Math.max(lo, v));
      out.noise.magnitude = clamp(out.noise.magnitude);
      out.noise.rate_per_s = clamp(out.noise.rate_per_s);
      out.noise.element = Math.min(ch.size, Math.max(0, out.noise.element));
    }
    return out;
  }, [injectors, edits, channelByKey]);

  const flight = pickedFlight || flights[0]?.path || "";

  const setParam = (key, name, value) =>
    setEdits((e) => ({ ...e, [key]: { ...e[key], [name]: value } }));

  const injStatus = status?.injectors || {};
  const activeKeys = Object.keys(injStatus).filter((k) => injStatus[k].active);
  const paused = status?.paused;

  const grouped = useMemo(() => {
    const g = {};
    injectors.forEach((s) => { (g[s.group] = g[s.group] || []).push(s); });
    return g;
  }, [injectors]);

  const sensorFaults = injStatus.noise?.faults || [];

  /**
   * Fill in the parts of a spec the engine leaves open.
   *
   * The channel list and the magnitude range both depend on what is being
   * flown — 105 channels on a replay, 128 synthetic — so the catalog ships
   * them empty and they get resolved here against the live channel list.
   */
  const renderSpec = (spec) => {
    if (!spec.params.some((p) => p.dynamic)) return spec;

    const picked = channelByKey[params[spec.key]?.channel];
    const relative = params[spec.key]?.scale === "relative";
    const groups = {};
    channels.forEach((c) => {
      (groups[c.group] = groups[c.group] || []).push({
        key: c.key,
        label: `${c.key}${c.unit ? ` (${c.unit})` : ""}`,
      });
    });

    return {
      ...spec,
      params: spec.params.map((p) => {
        if (p.dynamic === "channels") {
          return { ...p, groups, choices: channels.map((c) => c.key) };
        }
        if (p.dynamic === "span" && picked) {
          const [lo, hi, step] = relative ? [-200, 200, 1] : picked.span;
          return { ...p, min: lo, max: hi, step, unit: relative ? "%" : picked.unit };
        }
        if (p.name === "element") {
          // Only a per-engine or per-tank channel has anything to choose.
          return { ...p, hidden: !picked || picked.size <= 1, max: picked?.size ?? 1 };
        }
        return p;
      }),
    };
  };

  const channelNote = (spec) => {
    if (spec.key !== "noise") return null;
    const ch = channelByKey[params.noise?.channel];
    if (!ch) return null;
    const now = Array.isArray(ch.sample) ? ch.sample[0] : ch.sample;
    return `reading now ${now}${ch.unit ? ` ${ch.unit}` : ""}`
      + (ch.size > 1 ? ` · ${ch.size} units` : "")
      + (sensorFaults.length ? ` · ${sensorFaults.length} channel(s) faulted` : "");
  };

  const fuel = frame?.fuel_total_lbs;
  const simMin = status?.sim_time_s ? (status.sim_time_s / 60).toFixed(1) : "—";

  return (
    <div style={{
      minHeight: "100vh", background: C.bg, color: C.text,
      fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif",
      padding: "16px 20px 60px",
    }}>
      <div style={{ maxWidth: 1400, margin: "0 auto" }}>

        <div style={{
          display: "flex", alignItems: "baseline", gap: 14, marginBottom: 16,
          flexWrap: "wrap",
        }}>
          <h1 style={{ fontSize: 17, margin: 0, letterSpacing: 2, fontFamily: MONO }}>
            AERIS CONTROL PANEL
          </h1>
          <span style={{
            fontSize: 11, fontFamily: MONO,
            color: connected ? C.accent : C.crit,
          }}>
            {connected ? "ENGINE CONNECTED" : "ENGINE OFFLINE — start main.py"}
          </span>
          <span style={{ fontSize: 11, fontFamily: MONO, color: C.dim, marginLeft: "auto" }}>
            <a href="/" style={{ color: C.dim }}>flight display</a>
            {"  ·  "}
            <a href="/simulateMap" style={{ color: C.dim }}>map</a>
          </span>
        </div>

        {/* ── live readout ────────────────────────────────────────────── */}
        <Card
          title="Live"
          right={<span style={{ fontFamily: MONO, color: C.text }}>
            {status?.source || meta?.source || "—"}
          </span>}
        >
          <div style={{ display: "flex", gap: 26, flexWrap: "wrap" }}>
            <Readout label="PHASE" value={status?.phase || frame?.phase || "—"} />
            <Readout label="SIM T" value={simMin} unit="min" />
            <Readout label="ALT" value={frame ? Math.round(frame.pressure_alt_ft ?? frame.alt_captain ?? 0) : "—"} unit="ft" />
            <Readout label="IAS" value={frame ? Math.round(frame.ias_captain ?? 0) : "—"} unit="kt" />
            <Readout label="V/S" value={frame ? Math.round(frame.vs_captain ?? 0) : "—"} unit="fpm"
                     tone={frame && frame.vs_captain < -1500 ? C.warn : undefined} />
            <Readout label="FUEL" value={fuel != null ? Math.round(fuel).toLocaleString() : "—"} unit="lb" />
            <Readout label="RATE" value={status?.sample_rate_hz || "—"} unit="Hz" />
            <Readout label="ACTIVE FAULTS" value={activeKeys.length}
                     tone={activeKeys.length ? C.live : undefined} />
          </div>
        </Card>

        <div style={{ display: "grid", gridTemplateColumns: "minmax(0,2.1fr) minmax(280px,1fr)", gap: 14 }}>
          <div>
            {/* ── flight source ──────────────────────────────────────── */}
            <Card title="Flight source">
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
                <label style={{ flex: "0 0 150px" }}>
                  <div style={{ fontSize: 11, color: C.dim, fontFamily: MONO, marginBottom: 3 }}>
                    Source
                  </div>
                  <select
                    value={source} onChange={(e) => setSource(e.target.value)}
                    style={{
                      width: "100%", background: C.bg, color: C.text,
                      border: `1px solid ${C.line}`, borderRadius: 4,
                      padding: "5px 7px", fontSize: 12, fontFamily: MONO,
                    }}
                  >
                    <option value="xplane">Live (X-Plane)</option>
                    <option value="ntsb">NTSB docket</option>
                  </select>
                </label>

                <label style={{ flex: "1 1 260px", opacity: source === "ntsb" ? 1 : 0.4 }}>
                  <div style={{ fontSize: 11, color: C.dim, fontFamily: MONO, marginBottom: 3 }}>
                    Docket {flights.length ? `(${flights.length} found)` : "(none found)"}
                  </div>
                  <select
                    value={flight} disabled={source !== "ntsb" || !flights.length}
                    onChange={(e) => setPickedFlight(e.target.value)}
                    style={{
                      width: "100%", background: C.bg, color: C.text,
                      border: `1px solid ${C.line}`, borderRadius: 4,
                      padding: "5px 7px", fontSize: 12, fontFamily: MONO,
                    }}
                  >
                    {flights.map((f) => (
                      <option key={f.path} value={f.path}>
                        {f.id}
                      </option>
                    ))}
                  </select>
                </label>

                <Button tone="go" onClick={() => send({ cmd: "load", source, flight: source === "ntsb" ? flight : null })}>
                  Load &amp; fly
                </Button>
                <Button onClick={() => send({ cmd: paused ? "resume" : "pause" })}>
                  {paused ? "Resume" : "Pause"}
                </Button>
                <Button tone="stop" onClick={() => send({ cmd: "reset" })}>
                  Reset all
                </Button>
              </div>
              {source === "ntsb" && !flights.length && (
                <p style={{ fontSize: 11, color: C.warn, marginTop: 10, marginBottom: 0 }}>
                  No NTSB dockets on disk. Fetch one with
                  scripts/fetch_ntsb_docket.py &lt;ProjectID&gt;.
                </p>
              )}
            </Card>

            {/* ── injectors ──────────────────────────────────────────── */}
            {Object.entries(grouped).map(([group, specs]) => (
              <Card key={group} title={group}>
                <div style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fill, minmax(268px, 1fr))",
                  gap: 12,
                }}>
                  {specs.map((spec) => (
                    <InjectorCard
                      key={spec.key}
                      spec={renderSpec(spec)}
                      labels={channelLabels}
                      note={channelNote(spec)}
                      active={!!injStatus[spec.key]?.active}
                      params={params[spec.key] || {}}
                      setParam={(n, v) => setParam(spec.key, n, v)}
                      onInject={() => send({
                        cmd: "inject", key: spec.key, params: params[spec.key],
                      })}
                      onStop={() => send({ cmd: "clear", key: spec.key })}
                    />
                  ))}
                </div>
              </Card>
            ))}

            {!manifest && (
              <p style={{ color: C.dim, fontSize: 12 }}>
                Waiting for the engine to describe its injectors…
              </p>
            )}
          </div>

          {/* ── right column ─────────────────────────────────────────── */}
          <div>
            <Card
              title="Alerts"
              right={<span style={{ fontFamily: MONO, color: alerts.length ? C.crit : C.dim }}>
                {alerts.length}
              </span>}
            >
              {alerts.length === 0 && (
                <p style={{ color: C.dim, fontSize: 12, margin: 0 }}>Nothing active.</p>
              )}
              <div style={{ maxHeight: 340, overflowY: "auto" }}>
                {alerts.map((a) => (
                  <div key={a.id} style={{
                    borderLeft: `3px solid ${SEV[a.severity] || C.dim}`,
                    paddingLeft: 8, marginBottom: 9,
                  }}>
                    <div style={{ fontSize: 12, color: SEV[a.severity] || C.text, fontFamily: MONO }}>
                      {a.msg || a.id}
                    </div>
                    {a.detail && (
                      <div style={{ fontSize: 11, color: C.dim, lineHeight: 1.4 }}>{a.detail}</div>
                    )}
                  </div>
                ))}
              </div>
            </Card>

            <Card
              title="Active faults"
              right={<span style={{ fontFamily: MONO, color: C.dim }}>
                {activeKeys.length + Math.max(0, sensorFaults.length - 1)}
              </span>}
            >
              {activeKeys.length === 0 && (
                <p style={{ color: C.dim, fontSize: 12, margin: 0 }}>None injected.</p>
              )}

              {activeKeys.filter((k) => k !== "noise").map((k) => {
                const s = injStatus[k];
                const detail = Object.entries(s)
                  .filter(([n]) => !["active", "label", "faults"].includes(n))
                  .map(([n, v]) => `${n}=${typeof v === "number" ? Math.round(v * 100) / 100 : v}`)
                  .join("  ");
                return (
                  <div key={k} style={{ marginBottom: 9 }}>
                    <div style={{ fontSize: 12, color: C.accent, fontFamily: MONO }}>{s.label}</div>
                    <div style={{ fontSize: 10, color: C.dim, fontFamily: MONO }}>{detail}</div>
                  </div>
                );
              })}

              {/* Sensor faults are per channel, so each gets its own stop. */}
              {sensorFaults.map((f) => (
                <div key={`${f.channel}[${f.element}]`} style={{
                  display: "flex", alignItems: "flex-start", gap: 6, marginBottom: 9,
                }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{
                      fontSize: 11.5, color: C.live, fontFamily: MONO,
                      overflowWrap: "anywhere",
                    }}>
                      {f.channel}{f.element ? `[${f.element}]` : ""}
                    </div>
                    <div style={{ fontSize: 10, color: C.dim, fontFamily: MONO }}>
                      {f.mode}
                      {!["freeze", "drop", "invert"].includes(f.mode) &&
                        ` ${f.magnitude}${f.scale === "relative" ? "%" : ""}`}
                      {f.mode === "bias" && ` · now ${f.drift}`}
                    </div>
                  </div>
                  <button
                    onClick={() => send({
                      cmd: "clear", key: "noise",
                      params: { channel: f.channel, element: f.element },
                    })}
                    title={`stop ${f.channel}`}
                    style={{
                      background: "transparent", border: `1px solid ${C.line}`,
                      color: C.dim, borderRadius: 3, cursor: "pointer",
                      fontSize: 10, fontFamily: MONO, padding: "1px 6px",
                    }}
                  >
                    ✕
                  </button>
                </div>
              ))}
            </Card>

            <Card title="Command log">
              <div style={{ maxHeight: 220, overflowY: "auto", fontFamily: MONO, fontSize: 11 }}>
                {log.length === 0 && <span style={{ color: C.dim }}>—</span>}
                {log.map((e, i) => (
                  <div key={i} style={{ color: e.ok === false ? C.crit : C.dim, marginBottom: 3 }}>
                    <span style={{ color: e.ok === false ? C.crit : C.accent }}>
                      {e.ok === false ? "ERR" : "OK "}
                    </span>{" "}
                    {e.cmd} {e.key || ""} {e.error || e.detail || ""}
                  </div>
                ))}
              </div>
            </Card>
          </div>
        </div>
      </div>
    </div>
  );
}
