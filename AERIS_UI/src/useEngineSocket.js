import { useCallback, useEffect, useRef, useState } from "react";

const URL = "ws://localhost:8765";

/**
 * Talks to the AERIS engine.
 *
 * App.jsx has its own reader that shapes telemetry for the instruments; this
 * one keeps the raw frame and adds the outbound half, which the control panel
 * needs and the flight display doesn't.
 */
export default function useEngineSocket() {
  const [connected, setConnected] = useState(false);
  const [frame, setFrame] = useState(null);
  const [meta, setMeta] = useState(null);
  const [manifest, setManifest] = useState(null);
  const [status, setStatus] = useState(null);
  const [alerts, setAlerts] = useState([]);
  const [log, setLog] = useState([]);

  const wsRef = useRef(null);
  const retryRef = useRef(null);

  const send = useCallback((obj) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      setLog((l) => [{ ok: false, text: "not connected", at: Date.now() }, ...l].slice(0, 40));
      return false;
    }
    ws.send(JSON.stringify(obj));
    return true;
  }, []);

  useEffect(() => {
    let cancelled = false;

    function connect() {
      try { wsRef.current?.close(); } catch { /* already gone */ }
      const ws = new WebSocket(URL);
      wsRef.current = ws;

      ws.onopen = () => {
        if (cancelled) return;
        setConnected(true);
        clearTimeout(retryRef.current);
        ws.send(JSON.stringify({ cmd: "manifest" }));
        ws.send(JSON.stringify({ cmd: "status" }));
      };

      ws.onclose = () => {
        if (cancelled) return;
        setConnected(false);
        setAlerts([]);
        retryRef.current = setTimeout(connect, 2000);
      };

      ws.onerror = () => ws.close();

      ws.onmessage = ({ data: raw }) => {
        let m;
        try { m = JSON.parse(raw); } catch { return; }

        switch (m.topic) {
          case "manifest":
            setManifest(m);
            setStatus((s) => ({ ...(s || {}), ...m }));
            return;
          case "injector_status":
            setStatus((s) => ({ ...(s || {}), ...m }));
            return;
          case "flight_meta":
            setMeta(m);
            return;
          case "ack":
            setLog((l) => [{ ...m, at: Date.now() }, ...l].slice(0, 40));
            return;
          case "flight_complete":
            setLog((l) => [{ ok: true, cmd: "flight", detail: `saved ${m.file}`, at: Date.now() }, ...l].slice(0, 40));
            return;
          case "alert":
            setAlerts((a) => [
              { id: m.id, severity: m.severity, msg: m.msg, detail: m.detail, at: Date.now() },
              ...a.filter((x) => x.id !== m.id),
            ].slice(0, 60));
            return;
          case "alert_clear":
            setAlerts((a) => a.filter((x) => x.id !== m.id));
            return;
          default:
            // Anything without a topic is a telemetry frame.
            if (m.topic === undefined) setFrame(m);
        }
      };
    }

    connect();
    return () => {
      cancelled = true;
      clearTimeout(retryRef.current);
      try { wsRef.current?.close(); } catch { /* already gone */ }
    };
  }, []);

  return { connected, frame, meta, manifest, status, alerts, log, send };
}
