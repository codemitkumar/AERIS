"""Alert debouncing.

Modules decide a level every tick and broadcast on change, which chatters badly
when a signal sits astride a threshold - an airliner cruises within a few knots
of its speed limit for most of a flight. One clean flight produced 216 overspeed
transitions with no genuine exceedance in it.

Escalation is immediate; clearing and raising a caution need a dwell period.
Being slow to warn is the failure that hurts, but a light that blinks on and off
just teaches crews to ignore it.
"""

from core.rate import tick_dt

_RANK = {None: 0, "advisory": 1, "warning": 2, "critical": 3}


class AlertLatch:
    def __init__(self, clear_dwell_s: float = 2.0, warn_dwell_s: float = 2.0,
                 severity_of=None):
        self.clear_dwell_s = clear_dwell_s
        self.warn_dwell_s = warn_dwell_s
        self._severity_of = severity_of or (lambda a: "warning")
        self._current = None
        self._pending = None
        self._pending_for = 0.0

    def _rank(self, alert):
        if alert is None:
            return 0
        return _RANK.get(self._severity_of(alert), 2)

    def update(self, state: dict, alert):
        """Debounced alert, or UNCHANGED when the caller should do nothing."""
        if alert == self._current:
            self._pending = None
            self._pending_for = 0.0
            return UNCHANGED

        escalating = self._rank(alert) > self._rank(self._current)

        if escalating and self._rank(alert) >= _RANK["critical"]:
            self._current = alert
            self._pending = None
            self._pending_for = 0.0
            return alert

        dwell = self.warn_dwell_s if escalating else self.clear_dwell_s

        if alert != self._pending:
            self._pending = alert
            self._pending_for = 0.0
            return UNCHANGED

        self._pending_for += tick_dt(state)
        if self._pending_for >= dwell:
            self._current = alert
            self._pending = None
            self._pending_for = 0.0
            return alert
        return UNCHANGED

    @property
    def current(self):
        return self._current

    def reset(self) -> None:
        self._current = None
        self._pending = None
        self._pending_for = 0.0


class _Unchanged:
    """Distinct from None, which is a legitimate 'no alert' outcome."""

    def __repr__(self):
        return "UNCHANGED"

    def __bool__(self):
        return False


UNCHANGED = _Unchanged()
