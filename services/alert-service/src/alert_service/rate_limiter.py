"""Two-layer token-bucket rate limiter for Telegram fan-out (Phase 5).

Telegram enforces two ceilings on every bot:

* **Global**: ~30 messages / second across the whole bot.
* **Per-chat**: ~1 message / second to a single chat (groups: ~20/min).

This module models both with ``aiolimiter.AsyncLimiter`` so callers can wait
proactively instead of relying on retries after a 429. The per-chat buckets
are stored in a bounded LRU cache so a long-tail of one-off recipients does
not leak memory.

The limiter is intentionally process-local: in a multi-replica deployment
each pod gets its own budget, sized below the Telegram ceiling (default
global = 25/s with headroom for the other replica).
"""
from __future__ import annotations

import time
from collections import OrderedDict
from typing import Final

import structlog
from aiolimiter import AsyncLimiter

logger = structlog.get_logger(__name__)

_DEFAULT_CACHE_SIZE: Final[int] = 10_000


class PerChatRateLimiter:
    """Bounded LRU of per-chat token buckets fronted by a global bucket.

    Both buckets must be acquired before a message is sent. The global bucket
    enforces the bot-wide ceiling; the per-chat bucket prevents flooding a
    single recipient. ``acquire()`` is fair across coroutines because
    ``AsyncLimiter`` queues waiters in arrival order.
    """

    def __init__(
        self,
        global_rate: float,
        per_chat_rate: float,
        cache_size: int = _DEFAULT_CACHE_SIZE,
        time_period: float = 1.0,
    ) -> None:
        if global_rate <= 0:
            raise ValueError("global_rate must be > 0")
        if per_chat_rate <= 0:
            raise ValueError("per_chat_rate must be > 0")
        if cache_size < 1:
            raise ValueError("cache_size must be >= 1")
        if time_period <= 0:
            raise ValueError("time_period must be > 0")

        self._global = AsyncLimiter(max_rate=global_rate, time_period=time_period)
        self._per_chat_rate = per_chat_rate
        self._time_period = time_period
        self._cache_size = cache_size
        # _get_or_create contains no awaits, so asyncio's single-thread model
        # makes it safe without a lock.  Do not add awaits inside it.
        self._buckets: OrderedDict[str, AsyncLimiter] = OrderedDict()

    async def acquire(self, chat_id: int | str) -> None:
        """Block until both the global and the per-chat token are available."""
        key = str(chat_id)
        bucket = self._get_or_create(key)
        start = time.monotonic()
        await self._global.acquire()
        await bucket.acquire()
        waited = time.monotonic() - start
        if waited > self._time_period:
            logger.debug(
                "rate_limiter_throttled",
                chat_id=key,
                waited_seconds=round(waited, 3),
            )

    def _get_or_create(self, key: str) -> AsyncLimiter:
        bucket = self._buckets.get(key)
        if bucket is not None:
            self._buckets.move_to_end(key)
            return bucket
        if len(self._buckets) >= self._cache_size:
            evicted_key, _ = self._buckets.popitem(last=False)
            logger.debug("rate_limiter_evicted", chat_id=evicted_key)
        bucket = AsyncLimiter(max_rate=self._per_chat_rate, time_period=self._time_period)
        self._buckets[key] = bucket
        return bucket

    @property
    def tracked_chats(self) -> int:
        """Current size of the per-chat bucket cache (for diagnostics)."""
        return len(self._buckets)
