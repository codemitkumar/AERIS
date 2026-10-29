"""One dispatcher behind both the terminal and the control panel.

The terminal parses a line into a command dict and hands it here; the
WebSocket server hands the client's JSON here unchanged. Neither knows what
the other does, and there is only one place where an injection can start.

Command shapes
--------------
    {"cmd": "manifest"}                        -> what injectors exist
    {"cmd": "inject", "key": "fuel_leak",
     "params": {"rate_lbs_hr": 900}}           -> start / reconfigure
    {"cmd": "clear", "key": "fuel_leak"}       -> stop one
    {"cmd": "clear"}                           -> stop everything
    {"cmd": "reset"}                           -> undo accumulated offsets
    {"cmd": "status"}                          -> current state
    {"cmd": "flights"}                         -> NTSB dockets available on disk
    {"cmd": "channels"}                        -> channels a sensor fault can hit
    {"cmd": "load", "source": "ntsb",
     "flight": "DCA22LA178"}                   -> swap the flight being flown
    {"cmd": "load", "source": "xplane"}        -> back to the live X-Plane feed
    {"cmd": "pause"} / {"cmd": "resume"}

Every command answers with an "ack" carrying ok/error, so the panel can show
what happened instead of guessing from the absence of a reply.
"""

from emergencyInjector import catalog


def _ntsb_flights() -> list[dict]:
    from data.ingestion.ntsb_fdr_loader import list_cases
    return [{"id": c, "path": c} for c in list_cases()]


class CommandDispatcher:
    def __init__(self, manager, ws=None):
        self.manager = manager
        self.ws = ws
        self.gen = None          # set once the flight source is built
        self.session = None
        self.default_model = None

    def bind(self, gen) -> None:
        self.gen = gen

    def attach_session(self, session, default_model: str | None = None) -> None:
        """Give the dispatcher the ability to start and stop flights.

        Without this it can still inject into whatever is already running, so
        the injection commands work in a bare replay script too.
        """
        self.session = session
        self.default_model = default_model

    async def handle(self, msg: dict) -> dict:
        cmd = str(msg.get("cmd", "")).lower()
        try:
            reply = await self._run(cmd, msg)
        except Exception as exc:
            reply = {"topic": "ack", "cmd": cmd, "ok": False, "error": str(exc)}
            print(f"[CMD] {cmd}: {exc}")

        await self._send(reply)
        if reply.get("ok") and cmd in ("inject", "clear", "reset", "load",
                                       "pause", "resume"):
            await self._send(self.manager.status_dict(self.gen))
        return reply

    async def _run(self, cmd: str, msg: dict) -> dict:
        if cmd == "manifest":
            return {"topic": "manifest", "ok": True,
                    **self._session_state(),
                    "flights": _ntsb_flights(),
                    "channels": self.manager.channels,
                    **catalog.ui_manifest()}

        if cmd == "status":
            return {**self.manager.status_dict(self.gen), **self._session_state()}

        if cmd == "flights":
            from data.ingestion.ntsb_fdr_loader import ntsb_dir
            return {"topic": "flights", "ok": True,
                    "dir": str(ntsb_dir()),
                    "flights": _ntsb_flights()}

        if cmd == "load":
            if self.session is None:
                raise RuntimeError("this engine run cannot swap flights")
            source = msg.get("source", "xplane")
            self.manager.clear_all()
            self.session.build(source,
                               model=msg.get("model") or self.default_model,
                               flight=msg.get("flight"))
            self.bind(self.session.gen)
            await self.session.start()
            return {"topic": "ack", "cmd": "load", "ok": True,
                    **self._session_state()}

        if cmd in ("pause", "resume"):
            if self.session is None:
                raise RuntimeError("this engine run cannot be paused")
            getattr(self.session, cmd)()
            return {"topic": "ack", "cmd": cmd, "ok": True, **self._session_state()}

        if cmd == "inject":
            key = msg.get("key")
            if not key:
                raise ValueError("inject needs a 'key'")
            args = self.manager.apply(key, msg.get("params") or {}, gen=self.gen)
            return {"topic": "ack", "cmd": "inject", "ok": True,
                    "key": key, "params": args}

        if cmd == "channels":
            return {"topic": "channels", "ok": True,
                    "channels": self.manager.channels}

        if cmd == "clear":
            key = msg.get("key")
            if key:
                self.manager.stop(key, msg.get("params"))
            else:
                self.manager.clear_all()
            return {"topic": "ack", "cmd": "clear", "ok": True, "key": key,
                    "params": msg.get("params")}

        if cmd == "reset":
            self.manager.clear_all()
            if hasattr(self.gen, "reset_offsets"):
                self.gen.reset_offsets()
                return {"topic": "ack", "cmd": "reset", "ok": True,
                        "detail": "injectors cleared, flight snapped back to the recording"}
            return {"topic": "ack", "cmd": "reset", "ok": True,
                    "detail": "injectors cleared"}

        raise ValueError(f"unknown command '{cmd}'")

    def _session_state(self) -> dict:
        gen = self.gen
        return {
            "source": getattr(gen, "source_label", "unknown"),
            "paused": bool(self.session.paused) if self.session else False,
            "running": bool(self.session.running) if self.session else False,
            "sample_rate_hz": round(1.0 / gen.dt, 3) if gen else 0.0,
            "duration_s": round(getattr(getattr(gen, "flight", None),
                                        "duration_s", 0.0), 1),
        }

    async def _send(self, payload: dict) -> None:
        if self.ws:
            await self.ws.broadcast(payload)


def parse_terminal(line: str) -> dict | None:
    """Turn a typed line into the same dict the panel sends.

    Positional arguments are matched against the catalog's parameter order, so
    `inject fuel_leak 900 right` means rate then tank without the terminal
    needing to know what a fuel leak takes.
    """
    parts = line.strip().split()
    if not parts:
        return None

    cmd = parts[0].lower()

    if cmd in ("status", "manifest", "reset", "flights", "channels",
               "pause", "resume"):
        return {"cmd": cmd}

    if cmd == "load":
        return {"cmd": "load",
                "source": parts[1] if len(parts) > 1 else "xplane",
                "flight": parts[2] if len(parts) > 2 else None}

    if cmd == "clear":
        key = parts[1].lower() if len(parts) > 1 else None
        # 'clear noise ias_captain' drops one channel; 'clear noise' drops all.
        params = {"channel": parts[2]} if len(parts) > 2 else None
        return {"cmd": "clear", "key": key, "params": params}

    if cmd == "inject":
        if len(parts) < 2:
            raise ValueError("inject needs a fault type")
        key = parts[1].lower()
        # 'inject captain -3' still means airspeed, as it always has.
        if key in ("captain", "fo", "both"):
            rate = float(parts[2]) if len(parts) > 2 else -3.0
            return {"cmd": "inject", "key": "ias",
                    "params": {"side": key, "rate": rate}}

        spec = catalog.BY_KEY.get(key)
        if spec is None:
            raise ValueError(f"unknown fault type '{key}'")

        params = {}
        for p, raw in zip(spec["params"], parts[2:]):
            params[p["name"]] = raw if p["type"] == "choice" else float(raw)
        return {"cmd": "inject", "key": key, "params": params}

    raise ValueError(f"unknown command '{cmd}'")


def terminal_help() -> str:
    lines = ["[CMD] Terminal ready — commands:"]
    for spec in catalog.INJECTORS:
        args = " ".join(f"[{p['name']}]" for p in spec["params"])
        lines.append(f"  inject {spec['key']:<13} {args}")
    lines += [
        "  clear [key]              stop one injector, or all of them",
        "  reset                    clear everything, undo accumulated offsets",
        "  status                   what is currently running",
        "  flights                  list NTSB dockets on disk",
        "  channels                 list channels a sensor fault can hit",
        "  load xplane|ntsb [id]    swap to X-Plane or an NTSB docket",
        "  pause / resume",
    ]
    return "\n".join(lines)
