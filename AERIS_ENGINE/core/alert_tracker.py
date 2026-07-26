"""AlertTracker — transparent WebSocket wrapper that maintains active alert state.

Pass an AlertTracker as the `ws` argument to register_all(). It has the same
interface as WebSocketServer (broadcast_alert, broadcast) so all existing
modules work unchanged.  Simultaneously it keeps a live dict of currently
active alerts by ID, which AircraftHealthModule reads each tick to produce the
subsystem health assessment.
"""


class AlertTracker:
    def __init__(self, ws=None):
        self._ws = ws
        self._active: dict[str, dict] = {}  # alert_id → {severity, msg}

    async def broadcast_alert(self, payload: dict) -> None:
        topic = payload.get("topic", "")
        aid   = payload.get("id", "")
        if topic == "alert" and aid:
            self._active[aid] = {
                "severity": payload.get("severity", "warning"),
                "msg":      payload.get("msg", aid),
            }
        elif topic == "alert_clear" and aid:
            self._active.pop(aid, None)
        if self._ws:
            await self._ws.broadcast_alert(payload)

    async def broadcast(self, data: dict) -> None:
        if self._ws:
            await self._ws.broadcast(data)

    def snapshot(self) -> dict[str, dict]:
        """Return a shallow copy of the currently active alert dict."""
        return dict(self._active)
