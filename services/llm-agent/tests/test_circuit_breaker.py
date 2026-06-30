"""Tests for CircuitBreaker — state machine for LLM failure protection."""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from llm_agent.infrastructure.circuit_breaker import CircuitBreaker, CircuitState


class TestCircuitBreakerClosed:
    def test_starts_closed(self) -> None:
        cb = CircuitBreaker()
        assert cb.state == CircuitState.CLOSED
        assert not cb.is_open()

    def test_single_failure_stays_closed_below_threshold(self) -> None:
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 1

    def test_success_resets_failure_count(self) -> None:
        cb = CircuitBreaker(failure_threshold=5)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb.failure_count == 0
        assert cb.state == CircuitState.CLOSED


class TestCircuitBreakerOpening:
    def test_trips_at_threshold(self) -> None:
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.is_open()

    def test_is_open_true_when_open(self) -> None:
        cb = CircuitBreaker(failure_threshold=1)
        cb.record_failure()
        assert cb.is_open() is True

    def test_one_failure_trips_when_threshold_is_1(self) -> None:
        cb = CircuitBreaker(failure_threshold=1)
        assert not cb.is_open()
        cb.record_failure()
        assert cb.is_open()


class TestCircuitBreakerRecovery:
    def test_open_transitions_to_half_open_after_timeout(self) -> None:
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout_sec=1.0)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        with patch("llm_agent.infrastructure.circuit_breaker.time") as mock_time:
            mock_time.monotonic.return_value = time.monotonic() + 2
            assert cb.state == CircuitState.HALF_OPEN
            assert not cb.is_open()

    def test_half_open_success_closes_breaker(self) -> None:
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout_sec=0.0)
        cb.record_failure()
        with patch("llm_agent.infrastructure.circuit_breaker.time") as mock_time:
            mock_time.monotonic.return_value = time.monotonic() + 1
            _ = cb.state  # trigger HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_half_open_failure_reopens(self) -> None:
        # Use a long timeout so OPEN→HALF_OPEN only triggers inside the patch.
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout_sec=3600.0)
        cb.record_failure()
        with patch("llm_agent.infrastructure.circuit_breaker.time") as mock_time:
            mock_time.monotonic.return_value = time.monotonic() + 3601
            assert cb.state == CircuitState.HALF_OPEN
        # Failure in HALF_OPEN should re-trip to OPEN
        cb.record_failure()
        # Check with a long timeout so accessing .state doesn't immediately flip back
        assert cb.state == CircuitState.OPEN


class TestCircuitBreakerProperties:
    def test_failure_count_property(self) -> None:
        cb = CircuitBreaker(failure_threshold=10)
        cb.record_failure()
        cb.record_failure()
        assert cb.failure_count == 2

    @pytest.mark.parametrize("threshold", [1, 3, 5, 10])
    def test_always_trips_at_threshold(self, threshold: int) -> None:
        cb = CircuitBreaker(failure_threshold=threshold)
        for _ in range(threshold):
            cb.record_failure()
        assert cb.is_open()
