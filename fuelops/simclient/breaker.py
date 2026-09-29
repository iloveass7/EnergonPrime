"""Circuit breaker: open after 5 consecutive failures or >= 50% of the last 20 (pipeline §3.2)."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable


class CircuitBreaker:
    CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"

    def __init__(
        self,
        *,
        consecutive: int = 5,
        window: int = 20,
        ratio: float = 0.5,
        min_samples: int = 10,
        cooldown_s: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.consecutive = consecutive
        self.ratio = ratio
        self.min_samples = min_samples
        self.cooldown_s = cooldown_s
        self._clock = clock
        self._results: deque[bool] = deque(maxlen=window)
        self._streak = 0
        self._opened_at: float | None = None
        self._probing = False
        self.opens = 0

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return self.CLOSED
        if self._clock() - self._opened_at >= self.cooldown_s:
            return self.HALF_OPEN
        return self.OPEN

    def allow(self) -> bool:
        """May a request go out now? In HALF_OPEN only one probe at a time."""
        state = self.state
        if state == self.CLOSED:
            return True
        if state == self.HALF_OPEN and not self._probing:
            self._probing = True
            return True
        return False

    def success(self) -> None:
        self._results.append(True)
        self._streak = 0
        self._opened_at = None
        self._probing = False

    def failure(self) -> None:
        self._results.append(False)
        self._streak += 1
        failures = self._results.count(False)
        if (
            self._probing
            or self._streak >= self.consecutive
            or (
                len(self._results) >= self.min_samples
                and failures / len(self._results) >= self.ratio
            )
        ):
            if self._opened_at is None or self._probing:
                self.opens += 1
            self._opened_at = self._clock()
        self._probing = False
