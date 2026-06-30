"""Tests for DedupCache — TTL idempotency cache."""
from __future__ import annotations

import time
from unittest.mock import patch

from llm_agent.infrastructure.dedup_cache import DedupCache


class TestDedupCache:
    def test_unseen_returns_false(self) -> None:
        cache = DedupCache(ttl_sec=60)
        assert cache.is_seen("new-id") is False

    def test_mark_then_is_seen_returns_true(self) -> None:
        cache = DedupCache(ttl_sec=60)
        cache.mark_seen("alert-001")
        assert cache.is_seen("alert-001") is True

    def test_different_ids_independent(self) -> None:
        cache = DedupCache(ttl_sec=60)
        cache.mark_seen("a")
        assert cache.is_seen("a") is True
        assert cache.is_seen("b") is False

    def test_expired_entry_returns_false(self) -> None:
        cache = DedupCache(ttl_sec=1)
        cache.mark_seen("old-id")
        with patch("llm_agent.infrastructure.dedup_cache.time") as mock_time:
            mock_time.monotonic.return_value = time.monotonic() + 2
            assert cache.is_seen("old-id") is False

    def test_expired_entry_removed_on_access(self) -> None:
        cache = DedupCache(ttl_sec=1)
        cache.mark_seen("stale")
        with patch("llm_agent.infrastructure.dedup_cache.time") as mock_time:
            mock_time.monotonic.return_value = time.monotonic() + 2
            cache.is_seen("stale")
            assert len(cache) == 0

    def test_len_counts_non_expired(self) -> None:
        cache = DedupCache(ttl_sec=60)
        assert len(cache) == 0
        cache.mark_seen("a")
        cache.mark_seen("b")
        assert len(cache) == 2

    def test_len_excludes_expired(self) -> None:
        cache = DedupCache(ttl_sec=1)
        cache.mark_seen("old")
        with patch("llm_agent.infrastructure.dedup_cache.time") as mock_time:
            mock_time.monotonic.return_value = time.monotonic() + 2
            assert len(cache) == 0

    def test_mark_seen_refreshes_entry(self) -> None:
        cache = DedupCache(ttl_sec=60)
        cache.mark_seen("refresh-me")
        cache.mark_seen("refresh-me")
        assert cache.is_seen("refresh-me") is True

    def test_multiple_marks_do_not_grow_store(self) -> None:
        cache = DedupCache(ttl_sec=60)
        cache.mark_seen("x")
        cache.mark_seen("x")
        cache.mark_seen("x")
        assert len(cache) == 1
