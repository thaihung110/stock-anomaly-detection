"""TTL cache for the per-symbol subscriber list.

The same DB query repeated for every alert would dominate latency, so we
cache the result for ``ttl_sec`` seconds, per symbol. The cache is also
fully invalidatable via ``POST /internal/reload-subscribers`` whenever the
Telegram bot mutates ``user_preferences`` or ``user_watchlist``.

Concurrency design
------------------
``get()`` uses a two-stage approach to avoid holding the lock across the DB
round-trip (which would serialize ALL symbols through one lock under burst):

1. Acquire ``_lock`` briefly to read the entry dict and either:
   - return a still-valid cached list (cache hit), or
   - subscribe to an already-in-flight ``asyncio.Future`` for the same symbol
     (thundering-herd deduplification), or
   - register a new Future and proceed to fetch.
2. Await the Future **outside** the lock so concurrent misses for *different*
   symbols run their DB queries in parallel.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import structlog

from alert_service.subscriber_repository import Subscriber, SubscriberRepository

logger = structlog.get_logger(__name__)


@dataclass
class _Entry:
    subscribers: list[Subscriber]
    fetched_at: float


@dataclass
class _State:
    entries: dict[str, _Entry] = field(default_factory=dict)
    inflight: dict[str, asyncio.Future[list[Subscriber]]] = field(default_factory=dict)


class SubscriberCache:
    """Read-through TTL cache with per-symbol in-flight deduplication.

    Cache hits acquire the lock for microseconds. Cache misses for the same
    symbol share one DB round-trip via a shared Future; misses for *different*
    symbols run concurrently.
    """

    def __init__(self, repo: SubscriberRepository, ttl_sec: float) -> None:
        if ttl_sec <= 0:
            raise ValueError("ttl_sec must be > 0")
        self._repo = repo
        self._ttl = ttl_sec
        self._state = _State()
        self._lock = asyncio.Lock()
        self._hits = 0
        self._misses = 0

    async def get(self, symbol: str) -> list[Subscriber]:
        """Return the cached subscriber list for ``symbol``, refreshing if expired."""
        key = symbol.upper()

        existing: asyncio.Future[list[Subscriber]] | None = None
        async with self._lock:
            now = time.monotonic()
            entry = self._state.entries.get(key)
            if entry is not None and now - entry.fetched_at < self._ttl:
                self._hits += 1
                return entry.subscribers

            existing = self._state.inflight.get(key)
            if existing is not None:
                self._hits += 1
                fut: asyncio.Future[list[Subscriber]] = existing
            else:
                self._misses += 1
                loop = asyncio.get_running_loop()
                fut = loop.create_future()
                self._state.inflight[key] = fut

        if existing is not None:
            return await fut

        # We own the fetch for this symbol — run outside the lock.
        try:
            subscribers = await self._repo.get_subscribers_for_symbol(key)
        except Exception as exc:
            async with self._lock:
                self._state.inflight.pop(key, None)
            if not fut.cancelled():
                fut.set_exception(exc)
            raise
        else:
            async with self._lock:
                self._state.inflight.pop(key, None)
                self._state.entries[key] = _Entry(
                    subscribers=subscribers, fetched_at=time.monotonic()
                )
            fut.set_result(subscribers)
            return subscribers

    def invalidate(self) -> None:
        """Drop every cached entry. Called by ``/internal/reload-subscribers``."""
        size = len(self._state.entries)
        self._state.entries.clear()
        logger.info("subscriber_cache_invalidated", evicted=size)

    @property
    def stats(self) -> dict[str, int]:
        return {
            "hits": self._hits,
            "misses": self._misses,
            "entries": len(self._state.entries),
            "inflight": len(self._state.inflight),
        }
