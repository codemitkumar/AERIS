import asyncio
from typing import Callable, Awaitable


class DataBus:
    """In-process pub/sub bus for live flight state.

    Modules subscribe once at startup. Every simulation tick publishes the full
    state dict to all subscribers concurrently. A crash inside one module is
    caught and printed — it never touches the sim loop or other modules.

    Usage:
        bus = DataBus()
        bus.subscribe(my_async_fn)          # fn(state: dict) -> Awaitable
        await bus.publish(state)            # called each tick from flight_loop
    """

    def __init__(self):
        self._subscribers: list[Callable[[dict], Awaitable[None]]] = []

    def subscribe(self, fn: Callable[[dict], Awaitable[None]]) -> None:
        self._subscribers.append(fn)

    async def publish(self, state: dict) -> None:
        if not self._subscribers:
            return
        results = await asyncio.gather(
            *[fn(state) for fn in self._subscribers],
            return_exceptions=True,
        )
        for fn, result in zip(self._subscribers, results):
            if isinstance(result, Exception):
                print(f"[BUS] {fn.__qualname__} raised: {result}")
