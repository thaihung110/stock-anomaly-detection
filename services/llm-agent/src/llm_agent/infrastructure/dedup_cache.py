"""In-memory TTL cache for alert_id deduplication.

Prevents the classify pipeline from processing the same alert_id twice within
DEDUP_CACHE_TTL_SEC.  Eviction is lazy (on access, not background sweep).
Thread-safe via threading.Lock so it works from both asyncio coroutines and
any thread executor that might call it.
"""
from __future__ import annotations

import time
from threading import Lock


class DedupCache:
    """TTL-expiring set of seen alert IDs.

    Args:
        ttl_sec: How long to remember a seen alert_id (seconds).
    """

    def __init__(self, ttl_sec: int) -> None:
        self._ttl = ttl_sec
        self._store: dict[str, float] = {}  # alert_id → expiry (monotonic)
        self._lock = Lock()

    def is_seen(self, alert_id: str) -> bool:
        """Return True if alert_id was recently processed (and not yet expired)."""
        with self._lock:
            expiry = self._store.get(alert_id)
            if expiry is None:
                return False
            if time.monotonic() > expiry:
                del self._store[alert_id]
                return False
            return True

    def mark_seen(self, alert_id: str) -> None:
        """Record alert_id as processed; it will expire after ttl_sec."""
        with self._lock:
            self._store[alert_id] = time.monotonic() + self._ttl

    def __len__(self) -> int:
        """Return count of non-expired entries (used in tests and /health)."""
        with self._lock:
            now = time.monotonic()
            return sum(1 for v in self._store.values() if v > now)
