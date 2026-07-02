"""Tests for the two-layer token-bucket rate limiter (Phase 5)."""
from __future__ import annotations

import asyncio
import time

import pytest

from alert_service.services.rate_limiter import PerChatRateLimiter


@pytest.mark.unit
def test_invalid_args_raise() -> None:
    with pytest.raises(ValueError):
        PerChatRateLimiter(global_rate=0, per_chat_rate=1)
    with pytest.raises(ValueError):
        PerChatRateLimiter(global_rate=10, per_chat_rate=0)
    with pytest.raises(ValueError):
        PerChatRateLimiter(global_rate=10, per_chat_rate=1, cache_size=0)
    with pytest.raises(ValueError):
        PerChatRateLimiter(global_rate=10, per_chat_rate=1, time_period=0)


@pytest.mark.asyncio
async def test_global_cap_enforced_across_chats() -> None:
    """20 messages spread across 20 chats must be paced by the global cap."""
    limiter = PerChatRateLimiter(global_rate=10, per_chat_rate=10, time_period=1.0)
    start = time.monotonic()
    await asyncio.gather(*(limiter.acquire(f"chat-{i}") for i in range(20)))
    elapsed = time.monotonic() - start
    # 20 acquires at 10/s should take at least ~1.0s after the initial burst of 10.
    assert elapsed >= 0.9, f"global cap not enforced, elapsed={elapsed:.2f}s"


@pytest.mark.asyncio
async def test_per_chat_cap_enforced() -> None:
    """Multiple messages to the same chat in quick succession must space out."""
    limiter = PerChatRateLimiter(global_rate=100, per_chat_rate=2, time_period=1.0)
    start = time.monotonic()
    await limiter.acquire("chat-A")
    await limiter.acquire("chat-A")
    await limiter.acquire("chat-A")
    elapsed = time.monotonic() - start
    # 3 acquires at 2/s ⇒ third one must wait ~0.5s. Allow slack for slow CI.
    assert elapsed >= 0.4, f"per-chat cap not enforced, elapsed={elapsed:.2f}s"


@pytest.mark.asyncio
async def test_lru_eviction_when_over_capacity() -> None:
    # cache_size=3 → overflow limit = 3 + max(1, 0) = 4.
    # After 5 distinct chats the dict is bounded at most 4 entries
    # (the 10% buffer delays eviction to reduce the chance of dropping
    # a bucket that still has waiters on it).
    limiter = PerChatRateLimiter(global_rate=1000, per_chat_rate=1000, cache_size=3)
    for i in range(5):
        await limiter.acquire(f"chat-{i}")
    assert limiter.tracked_chats <= 4


@pytest.mark.asyncio
async def test_int_and_str_chat_id_share_bucket() -> None:
    """``int`` and ``str`` representations of the same chat id share one bucket."""
    limiter = PerChatRateLimiter(global_rate=100, per_chat_rate=100)
    await limiter.acquire(123456789)
    await limiter.acquire("123456789")
    assert limiter.tracked_chats == 1
