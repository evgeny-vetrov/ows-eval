"""The sandbox clock: wall time plus an offset the sandbox API can move forward, so a
deadline an hour away can be reached in a demo or a test without waiting."""

from collections.abc import Callable
from datetime import datetime, timedelta, timezone


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SandboxClock:
    def __init__(self, source: Callable[[], datetime] = _utc_now):
        self._source = source
        self._offset = timedelta(0)

    @property
    def offset(self) -> timedelta:
        return self._offset

    def now(self) -> datetime:
        return self._source() + self._offset

    def advance(self, delta: timedelta) -> datetime:
        if delta < timedelta(0):
            raise ValueError("the clock only moves forward")
        self._offset += delta
        return self.now()
