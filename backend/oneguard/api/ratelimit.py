"""C1's draft limit (docs/api-contract.md §1.1, §3.2): at most ``DRAFTS_PER_MINUTE`` drafts
per customer in any 60 s, else ``429 rate_limited``.

A sliding window kept in this process's memory: the app runs on one machine
(docs/architecture.md), so one process sees every draft. A restart forgets the window,
which only ever lets a customer draft again sooner. Real time (monotonic), never the
simulated clock: this bounds how fast a caller can make the compiler work.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable
from threading import Lock

DRAFTS_PER_MINUTE = 10
WINDOW_S = 60.0


class SlidingWindowLimiter:
    """At most ``limit`` accepted calls per key in any ``window_s`` seconds."""

    def __init__(
        self, limit: int, window_s: float = WINDOW_S, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.limit = limit
        self.window_s = window_s
        self._clock = clock
        self._calls: defaultdict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def acquire(self, key: str) -> float | None:
        """Count a call for ``key`` and return None, or, over the limit, count nothing and
        return the seconds until the oldest call in the window leaves it."""
        now = self._clock()
        with self._lock:
            calls = self._calls[key]
            while calls and now - calls[0] >= self.window_s:
                calls.popleft()
            if len(calls) >= self.limit:
                return self.window_s - (now - calls[0])
            calls.append(now)
            return None
