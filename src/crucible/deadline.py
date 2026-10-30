"""A monotonic wall-clock deadline shared by the runner, agents and sandboxes."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Deadline:
    """A point in monotonic time after which work must stop.

    The clock is injectable so tests can drive time deterministically.
    """

    at: float
    clock: Callable[[], float] = field(default=time.monotonic, repr=False, compare=False)

    @classmethod
    def after(cls, seconds: float, clock: Callable[[], float] = time.monotonic) -> Deadline:
        return cls(at=clock() + seconds, clock=clock)

    def remaining(self) -> float:
        """Seconds left, never negative."""
        return max(0.0, self.at - self.clock())

    def expired(self) -> bool:
        return self.remaining() <= 0.0

    def clamp(self, timeout: float) -> float:
        """The smaller of ``timeout`` and the time remaining."""
        return min(timeout, self.remaining())
