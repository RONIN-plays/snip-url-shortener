"""A tiny circuit breaker.

Problem: when a dependency (Redis, Postgres) is DOWN, every request that touches it
waits for a timeout before giving up. Requests pile up, latency explodes, and our
own service falls over even though most of its work didn't need that dependency.

Idea (like the breaker in your fuse box):
  CLOSED    normal. Count consecutive failures.
  OPEN      after `threshold` failures: refuse instantly (no waiting, no network
            call) for `reset_after` seconds.
  HALF-OPEN once that time passes, let ONE call through as a probe. Success ->
            CLOSED again. Failure -> OPEN for another `reset_after` seconds.
"""

import time


class CircuitBreaker:
    def __init__(self, threshold: int = 3, reset_after: float = 2.0):
        self.threshold = threshold
        self.reset_after = reset_after
        self.reset()

    def reset(self) -> None:
        self.failures = 0
        self.open_until = 0.0

    def allow(self) -> bool:
        if self.failures < self.threshold:
            return True                               # CLOSED
        now = time.monotonic()
        if now < self.open_until:
            return False                              # OPEN: fail fast
        # HALF-OPEN: let this one call probe. Pushing open_until forward blocks
        # every other caller until the probe reports back.
        self.open_until = now + self.reset_after
        return True

    def success(self) -> None:
        self.failures = 0
        self.open_until = 0.0

    def failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.open_until = time.monotonic() + self.reset_after

    @property
    def is_open(self) -> bool:
        return self.failures >= self.threshold and time.monotonic() < self.open_until
