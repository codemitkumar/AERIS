import asyncio
import json
import websockets
from typing import Callable, Awaitable, Optional


class WebSocketServer:
    """
    Async WebSocket server.

    Broadcasts simulation state to all connected clients.
    Also accepts inbound JSON commands from any client and routes them to a
    registered command handler:

        {"cmd": "inject_failure", "side": "captain",
         "type": "pitot_icing", "severity": 0.9}

        {"cmd": "clear_failure", "side": "fo"}

        {"cmd": "status"}
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 8765):
        self.host = host
        self.port = port
        self.clients: set = set()
        self._cmd_handler: Optional[Callable[[dict], Awaitable[None]]] = None

    def register_command_handler(self, handler: Callable[[dict], Awaitable[None]]):
        """Register an async function that receives inbound command dicts."""
        self._cmd_handler = handler

    async def _handler(self, websocket):
        self.clients.add(websocket)
        print(f"[WS] Client connected: {websocket.remote_address}  "
              f"(total: {len(self.clients)})")
        try:
            async for raw in websocket:
                if not self._cmd_handler:
                    continue
                try:
                    msg = json.loads(raw)
                    await self._cmd_handler(msg)
                except (json.JSONDecodeError, Exception) as exc:
                    print(f"[WS] Bad command: {exc}")
        finally:
            self.clients.discard(websocket)
            print(f"[WS] Client disconnected: {websocket.remote_address}  "
                  f"(total: {len(self.clients)})")

    async def broadcast(self, data: dict):
        if not self.clients:
            return
        message = json.dumps(data, default=str)
        clients_snapshot = set(self.clients)
        results = await asyncio.gather(
            *[client.send(message) for client in clients_snapshot],
            return_exceptions=True,
        )
        for client, result in zip(clients_snapshot, results):
            if isinstance(result, Exception):
                self.clients.discard(client)

    async def broadcast_alert(self, alert: dict):
        """Send an alert message to all connected clients.

        Expected keys: topic ("alert" | "alert_clear"), id, severity, msg, detail.
        """
        await self.broadcast(alert)

    async def start(self):
        print(f"[WS] Server listening on ws://{self.host}:{self.port}")
        async with websockets.serve(self._handler, self.host, self.port):
            await asyncio.Future()  # run forever
