"""Tests for ``SubscriberCache``."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from alert_service.services.subscriber_cache import SubscriberCache
from alert_service.infrastructure.subscriber_repository import Subscriber


def _subs(*chat_ids: int) -> list[Subscriber]:
    return [Subscriber(user_id=uuid4(), chat_id=cid) for cid in chat_ids]


@pytest.fixture
def repo() -> AsyncMock:
    return AsyncMock()


@pytest.mark.asyncio
async def test_cache_miss_then_hit(repo: AsyncMock) -> None:
    repo.get_subscribers_for_symbol.return_value = _subs(101)
    cache = SubscriberCache(repo, ttl_sec=10.0)

    first = await cache.get("AAPL")
    second = await cache.get("AAPL")

    assert first == second
    repo.get_subscribers_for_symbol.assert_awaited_once_with("AAPL")
    assert cache.stats["hits"] == 1
    assert cache.stats["misses"] == 1


@pytest.mark.asyncio
async def test_cache_expires_after_ttl(repo: AsyncMock, monkeypatch: pytest.MonkeyPatch) -> None:
    repo.get_subscribers_for_symbol.return_value = _subs(101)
    cache = SubscriberCache(repo, ttl_sec=1.0)

    fake_now = [1000.0]

    def now() -> float:
        return fake_now[0]

    monkeypatch.setattr("alert_service.services.subscriber_cache.time.monotonic", now)

    await cache.get("AAPL")
    fake_now[0] += 0.5
    await cache.get("AAPL")  # still fresh → hit
    fake_now[0] += 2.0
    await cache.get("AAPL")  # expired → miss

    assert repo.get_subscribers_for_symbol.await_count == 2


@pytest.mark.asyncio
async def test_invalidate_drops_all_entries(repo: AsyncMock) -> None:
    repo.get_subscribers_for_symbol.return_value = _subs(101)
    cache = SubscriberCache(repo, ttl_sec=60.0)

    await cache.get("AAPL")
    await cache.get("MSFT")
    assert cache.stats["entries"] == 2

    cache.invalidate()
    assert cache.stats["entries"] == 0
    assert cache.stats["inflight"] == 0

    await cache.get("AAPL")
    assert repo.get_subscribers_for_symbol.await_count == 3


@pytest.mark.asyncio
async def test_invalidate_cancels_inflight_future_and_prevents_stale_cache(
    repo: AsyncMock,
) -> None:
    """invalidate() during an in-flight fetch must cancel the future so stale
    data is not cached, and the fetch owner still returns its DB result."""
    started = asyncio.Event()
    release = asyncio.Event()
    fetched_subs = _subs(101)  # fixed instance so equality check is deterministic

    async def slow_query(symbol: str) -> list[Subscriber]:
        started.set()
        await release.wait()
        return fetched_subs

    repo.get_subscribers_for_symbol.side_effect = slow_query
    cache = SubscriberCache(repo, ttl_sec=60.0)

    fetch_task = asyncio.create_task(cache.get("AAPL"))
    await started.wait()

    # Invalidate while the fetch is in flight.
    cache.invalidate()

    # Let the fetch complete — the fetch owner returns its result directly.
    release.set()
    result = await fetch_task
    assert result == fetched_subs

    # Stale data must NOT be cached after invalidation.
    assert cache.stats["entries"] == 0


@pytest.mark.asyncio
async def test_concurrent_misses_collapse_to_one_query(repo: AsyncMock) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    call_count = 0

    async def slow_query(symbol: str) -> list[Subscriber]:
        nonlocal call_count
        call_count += 1
        started.set()
        await release.wait()
        return _subs(101)

    repo.get_subscribers_for_symbol.side_effect = slow_query
    cache = SubscriberCache(repo, ttl_sec=60.0)

    task_a = asyncio.create_task(cache.get("AAPL"))
    await started.wait()
    task_b = asyncio.create_task(cache.get("AAPL"))
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(task_a, task_b)

    assert results[0] == results[1]
    assert call_count == 1


@pytest.mark.asyncio
async def test_invalid_ttl_rejected(repo: AsyncMock) -> None:
    with pytest.raises(ValueError):
        SubscriberCache(repo, ttl_sec=0)
