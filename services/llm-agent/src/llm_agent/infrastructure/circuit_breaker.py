"""Circuit breaker for LLM API calls.

States:
  CLOSED   — Normal operation; failures counted.
  OPEN     — Fast-failing for recovery_timeout_sec; then → HALF_OPEN.
  HALF_OPEN — One probe call allowed; success → CLOSED, failure → OPEN.

Thread-safe via threading.Lock.
"""
from __future__ import annotations

import time
from enum import Enum
from threading import Lock


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Protect LLM calls from cascading failures.

    Args:
        failure_threshold: Consecutive failures that trip the breaker.
        recovery_timeout_sec: Seconds to stay OPEN before probing again.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout_sec: float = 60.0,
    ) -> None:
        self._threshold = failure_threshold
        self._recovery_timeout = recovery_timeout_sec
        self._state = CircuitState.CLOSED
        self._failure_count: int = 0
        self._opened_at: float = 0.0
        self._lock = Lock()

    @property
    def state(self) -> CircuitState:
        """Current circuit state (may transition OPEN→HALF_OPEN on timeout)."""
        with self._lock:
            return self._current_state()

    def _current_state(self) -> CircuitState:
        if self._state == CircuitState.OPEN:
            if time.monotonic() - self._opened_at >= self._recovery_timeout:
                self._state = CircuitState.HALF_OPEN
        return self._state

    def is_open(self) -> bool:
        """True when the breaker should block calls (OPEN state only)."""
        return self.state == CircuitState.OPEN

    def record_success(self) -> None:
        """Call after a successful LLM response to reset the breaker."""
        with self._lock:
            self._failure_count = 0
            self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        """Call after each LLM failure to advance towards (or stay) OPEN."""
        with self._lock:
            self._failure_count += 1
            if (
                self._state == CircuitState.HALF_OPEN
                or self._failure_count >= self._threshold
            ):
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()

    @property
    def failure_count(self) -> int:
        with self._lock:
            return self._failure_count
