"""Sample-rate helpers.

Modules used to size history buffers in ticks, which assumed the 30 Hz
synthetic feed. Real FDR replay runs at the recorder's own rate (1-16 Hz), so
a "60 tick" window meant 2 s on synthetic data and 7.5 s on an 8 Hz replay.
Windows are declared in seconds now and converted using the rate on each state.
"""

from collections import deque

_DEFAULT_RATE_HZ = 30.0


def sample_rate(state: dict) -> float:
    rate = state.get("sample_rate_hz")
    if rate:
        return float(rate)
    dt = state.get("dt")
    if dt:
        return 1.0 / float(dt)
    return _DEFAULT_RATE_HZ


def tick_dt(state: dict) -> float:
    dt = state.get("dt")
    if dt:
        return float(dt)
    return 1.0 / sample_rate(state)


def window_len(state: dict, seconds: float, minimum: int = 8) -> int:
    """Samples spanning `seconds` on this feed.

    `minimum` matters because real channels are quantised - airspeed to 0.25 kt
    at 4 Hz - and quantisation doesn't average out like gaussian noise does.
    With only two samples in a window, one quantisation step reads as a large
    rate of change.
    """
    return max(minimum, int(round(sample_rate(state) * seconds)))


class TimeWindow:
    """Rolling buffer sized in seconds. Resizes if the feed's rate changes."""

    def __init__(self, seconds: float, minimum: int = 8):
        self.seconds = seconds
        self._minimum = minimum
        self._rate: float | None = None
        self._buf: deque = deque()

    def push(self, state: dict, value):
        rate = sample_rate(state)
        if rate != self._rate:
            self._rate = rate
            self._buf = deque(self._buf, maxlen=window_len(state, self.seconds, self._minimum))
        self._buf.append(value)
        return self._buf

    @property
    def full(self) -> bool:
        return self._buf.maxlen is not None and len(self._buf) == self._buf.maxlen

    def clear(self) -> None:
        self._buf.clear()

    def __len__(self) -> int:
        return len(self._buf)

    def __iter__(self):
        return iter(self._buf)

    def __getitem__(self, idx):
        return list(self._buf)[idx]

    @property
    def span_s(self) -> float:
        if self._rate is None or len(self._buf) < 2:
            return 0.0
        return (len(self._buf) - 1) / self._rate
